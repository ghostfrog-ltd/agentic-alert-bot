#!/usr/bin/env python3
# app.py – GhostFrog Bob ↔ Chad message bus + UI (PoC)
#
# This file runs the Flask web UI for the GhostFrog Bob/Chad PoC.
# It:
#   - serves the dark-mode chat UI at /chat using chat_ui.html
#   - exposes POST /api/chat which:
#       * writes the user's message into /ai/data/queue/*.user.txt
#       * asks Bob (OpenAI) to build a plan
#       * asks Chad (local executor) to apply it inside PROJECT_ROOT
#       * returns a summary + any file previews back to the browser.

"""
This is the main Flask UI application for the GhostFrog project.

It serves the chat UI on /chat, and a JSON API on /api/chat that:
  - writes the user's message into /ai/data/queue/*.user.txt
  - asks Bob (cloud) to build a plan
  - asks Chad (local) to execute it
  - streams a summary of what happened back to the browser

Lives in:
  /Volumes/Bob/www/ghostfrog-agentic-alert-bot-bob/ai/app.py

You run:
  cd /Volumes/Bob/www/ghostfrog-agentic-alert-bot-bob/ai
  python3 app.py

UI:
  http://127.0.0.1:8765/chat

All data lives under:
  /ai/data/

Per user request we create:
  /ai/data/queue/00001_2025-11-23.user.txt
  /ai/data/queue/00001_2025-11-23.plan.json
  /ai/data/queue/00001_2025-11-23.exec.json
  /ai/data/scratch/00001_2025-11-23.txt
"""

import json
import os
import smtplib
import mimetypes
from email.message import EmailMessage
from datetime import datetime, date, timezone
from pathlib import Path

from flask import Flask, jsonify, request, render_template_string
from dotenv import load_dotenv
from openai import OpenAI

# Load .env (OPENAI_API_KEY, BOB_MODEL, SMTP_*, etc.)
load_dotenv()

# OpenAI client for Bob (cloud reasoner)
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
BOB_MODEL = os.getenv("BOB_MODEL", "gpt-4.1-mini")

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

AI_ROOT = Path(__file__).resolve().parent  # /ai
DATA_ROOT = AI_ROOT / "data"
QUEUE_DIR = DATA_ROOT / "queue"
SCRATCH_DIR = DATA_ROOT / "scratch"
SEQ_FILE = DATA_ROOT / "seq.txt"
MARKDOWN_NOTES_DIR = DATA_ROOT / "notes"

DATA_ROOT.mkdir(parents=True, exist_ok=True)
QUEUE_DIR.mkdir(parents=True, exist_ok=True)
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
MARKDOWN_NOTES_DIR.mkdir(parents=True, exist_ok=True)

# Project jail – Bob/Chad are ONLY allowed to touch files inside here
ENV_PROJECT_JAIL = os.getenv("ENV_PROJECT_JAIL")
if ENV_PROJECT_JAIL:
    PROJECT_ROOT = Path(ENV_PROJECT_JAIL).resolve()
else:
    PROJECT_ROOT = AI_ROOT.parent.resolve()

# Chat UI template file
CHAT_TEMPLATE_PATH = AI_ROOT / "chat_ui.html"

# ---------------------------------------------------------------------------
# Schema Bob uses for plans
# ---------------------------------------------------------------------------

BOB_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "task_type": {
            "type": "string",
            "enum": ["codemod", "analysis", "tool", "chat"],
        },
        "summary": {"type": "string"},
        "analysis_file": {  # file to analyse (for analysis tasks)
            "type": "string",
            "default": "",
        },
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "operation": {
                        "type": "string",
                        "enum": [
                            "prepend_comment",
                            "create_or_overwrite_file",
                            "append_to_bottom",
                        ],
                    },
                    "content": {"type": "string"},
                },
                "required": ["file", "operation", "content"],
                "additionalProperties": False,
            },
        },
        "tool": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": [
                        "get_current_datetime",
                        "list_files",
                        "read_file",
                        "create_markdown_note",
                        "append_to_markdown_note",
                        "send_email",
                    ],
                },
                "args": {
                    "type": "object",
                    "additionalProperties": True,
                    "default": {},
                },
            },
            "default": {},
        },
    },
    "required": ["task_type", "summary", "analysis_file", "edits"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# ID generator: 00001_YYYY-MM-DD
# ---------------------------------------------------------------------------

def next_message_id() -> tuple[str, str, str]:
    """
    Returns (id_str, date_str, base_name) like:
      ("00001", "2025-11-23", "00001_2025-11-23")
    """
    today = date.today().strftime("%Y-%m-%d")

    if SEQ_FILE.exists():
        try:
            current = int(SEQ_FILE.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            current = 0
    else:
        current = 0

    new_val = current + 1
    SEQ_FILE.write_text(str(new_val), encoding="utf-8")

    id_str = f"{new_val:05d}"
    base = f"{id_str}_{today}"
    return id_str, today, base


# ---------------------------------------------------------------------------
# "Bob" – reasoning / planning layer
# ---------------------------------------------------------------------------

def bob_build_plan(
        id_str: str,
        date_str: str,
        base: str,
        user_text: str,
        tools_enabled: bool = True,
) -> dict:
    """
    Bob builds a structured plan for Chad.

    - task_type:
        * 'chat'     -> just answer, no file / tool / edits
        * 'tool'     -> call a specific local tool via Chad
        * 'analysis' -> review/explain an existing file (no edits)
        * 'codemod'  -> Chad may edit files
    - edits: only present/used when task_type == 'codemod'
    - tools_enabled: when False, Bob MUST NOT choose task_type 'tool'
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    api_key = os.getenv("OPENAI_API_KEY")

    # If no key, fall back to a simple stub
    if not api_key:
        plan: dict = {
            "id": id_str,
            "date": date_str,
            "created_at": now,
            "actor": "bob",
            "kind": "plan",
            "raw_user_text": user_text,
            "task": {
                "type": "chat",  # default to chat in stub mode
                "summary": f"(STUB – no OPENAI_API_KEY) Handle user request: {user_text}",
                "analysis_file": "",
                "edits": [],
                "tool": {},
            },
        }
        (QUEUE_DIR / f"{base}.plan.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8"
        )
        return plan

    if tools_enabled:
        tool_mode_text = (
            "Tools ARE ENABLED for this request. You should choose 'tool' whenever "
            "the user is asking you to interact with the live project/filesystem, "
            "write notes, or send email — even if they do NOT mention tool names.\n"
        )
    else:
        tool_mode_text = (
            "Tools ARE DISABLED for this request. You MUST NOT choose task_type "
            "'tool', and you MUST leave the 'tool' object empty. Handle the "
            "request purely as 'chat', 'analysis', or 'codemod'.\n"
        )

    try:
        system_prompt = (
            "You are Bob, a senior reasoning model orchestrating a local coder called Chad.\n"
            "The user is working on a Python / GhostFrog project.\n\n"

            # =========================
            # USER INTENT RULES (CORE)
            # =========================
            "USER INTENT RULES\n"
            "- If the user says phrases like: 'show me', 'what does', 'explain', 'tell me', 'describe', you MUST treat the request as INFORMATION ONLY. Do NOT change files, do NOT propose changes. Respond with an explanation or, if asked, a preview of code.\n"
            "- If the user says phrases like: 'add', 'fix', 'change', 'implement', 'update', 'modify', 'stop doing', 'make it so', you MUST treat the request as a DIRECT CODING ORDER. Do NOT ask for confirmation first. Apply the change immediately and show a unified diff afterward.\n"
            "- Do NOT confuse the two behaviours. 'SHOW' means NO CHANGES. 'DO' means MAKE CHANGES without asking.\n"
            "- When showing code, ONLY show the specific function or small snippet requested. NEVER dump entire files unless the user explicitly says 'show the whole file'.\n\n"

            # =========================
            # FUNCTION MODIFICATION RULES
            # =========================
            "FUNCTION MODIFICATION RULES\n"
            "- When the user asks you to change a specific function (for example: 'run()', 'main()', 'send_alert'), you MUST:\n"
            "    * Locate the existing definition of that function in the specified file.\n"
            "    * Modify that existing definition in-place (between its 'def ...' line and the end of that function only).\n"
            "- You MUST NOT create a new function with the same name alongside the old one.\n"
            "- You MUST NOT introduce a wrapper function instead of editing the real function, unless the user explicitly asks for a wrapper.\n"
            "- You MUST NOT 'monkey patch' by storing the old function in a variable (e.g. 'old_run = run') and redefining 'run' later in the file.\n"
            "- You MUST NOT use 'sys.modules' or similar tricks to re-bind or patch the function name at the bottom of the file.\n"
            "- If you cannot find the requested function, say so in the plan instead of inventing a new one.\n\n"

            # =========================
            # CHANGE RULES
            # =========================
            "CHANGE REQUEST INTERPRETATION RULES\n"
            "- When the user says something in the form:\n"
            "    'in <path> we have <function>() that does X - we must (or must not) do Y. please implement that ...'\n"
            "  you MUST treat this as a direct request to MODIFY the EXISTING FUNCTION in that file.\n"
            "- In this case:\n"
            "    * task_type MUST be 'codemod'.\n"
            "    * 'file' in the edit MUST be the exact path the user mentioned (e.g. 'agent/actions/alert/roi_listings.py').\n"
            "    * You MUST locate the existing definition of that function in that file (e.g. 'def run(...):').\n"
            "    * You MUST change the body of that existing function to satisfy the new rule (e.g. add an end_time guard BEFORE sending emails).\n"
            "- You MUST NOT:\n"
            "    * Create a second function with the same name.\n"
            "    * Introduce a wrapper that calls the original function instead of editing it.\n"
            "    * Import the same module inside itself to get the original function.\n"
            "- If, and only if, the function truly does not exist in that file, you may say so in the plan. You MUST NOT invent a second version of it.\n\n"

            # =========================
            # preserve rules
            # =========================
            "PRESERVE EXISTING LOGIC RULES\n"
            "- When performing a 'codemod' you MUST treat the existing code as correct and working unless the user explicitly says it is wrong or should be rewritten.\n"
            "- Your default behaviour is to EXTEND or MODIFY the existing implementation with the MINIMAL change needed to satisfy the new requirement.\n"
            "- You MUST NOT delete or replace large blocks of logic that are unrelated to the user's request.\n"
            "- You MUST NOT throw away an existing function body and replace it with a brand new, tiny implementation that only handles the new condition.\n"
            "- For requests like 'we must not send emails when CONDITION', you MUST:\n"
            "    * Keep all the existing behaviour (loops, queries, logging, retries, etc.).\n"
            "    * Add a guard, filter, or conditional around the existing email-sending logic.\n"
            "    * Ensure the only behavioural change is that emails are skipped when CONDITION is true.\n"
            "- Only if the user explicitly says things like 'rewrite this from scratch', 'simplify/strip this', or 'replace this function entirely' are you allowed to remove all of the existing body.\n\n"

            # =========================
            # STRING / LOG MESSAGE RULES
            # =========================
            "STRING / LOG MESSAGE RULES\n"
            "- You MUST treat existing string literals (especially log / debug / error messages) as part of the public interface.\n"
            "- You MUST NOT change the wording, punctuation, placeholders (e.g. %s, %.2f, %.0f%%), or currency symbols (e.g. '£') in existing log strings unless the user EXPLICITLY asks you to.\n"
            "- You MUST NOT change or normalise ANY Unicode characters in existing strings: emojis (e.g. '🚨'), typographic dashes ('–', '—'), ellipses ('…'), curly quotes ('“', '”', '‘', '’'), or any other non-ASCII characters MUST remain exactly as they are.\n"
            "- Do NOT replace '£' with '3', '?', or any other character. If you see '£' in the original file, it MUST remain '£' in your edited version.\n"
            "- Do NOT 'ASCII-normalise' Unicode. Never replace emojis or Unicode punctuation with ASCII approximations.\n"
            "- If you need to add a new log or change behaviour, add a new line or a small addition – do NOT rewrite or 'fix' the existing message template.\n\n"

            # =========================
            # SPECIAL FILE RULES
            # =========================
            "SPECIAL FILE RULES – roi_listings.py\n"
            "- The file 'agent/actions/alert/roi_listings.py' is extremely sensitive. You MUST be extra careful when editing it.\n"
            "- In this file, ALL existing log messages MUST remain byte-for-byte identical unless the user explicitly says to change the wording.\n"
            "- In particular, the log line with the message:\n"
            "    \"[roi_listings] no opportunities ≥ £%.2f / ROI ≥ %.0f%%\"\n"
            "  MUST NEVER be altered. You MUST NOT change its text, placeholders, or characters in any way.\n"
            "- When the user asks for changes related to email behaviour in roi_listings.run(), you MUST:\n"
            "    * Keep the existing logging exactly as it is.\n"
            "    * Implement the new behaviour by adding a small guard/filter around the email digest logic only.\n"
            "    * For example, you MAY filter the list of opportunities used for _send_email_digest so that only items with end_time < now() are included.\n"
            "    * You MUST NOT touch or rewrite the surrounding logger.info lines.\n"
            "- If you need new logs in this file, add NEW logger.info lines rather than modifying existing ones.\n\n"

            # =========================
            # NO-REFORMAT / DIFF RULES
            # =========================
            "NO-REFORMAT / DIFF RULES\n"
            "- You MUST preserve the file's overall structure, ordering, and formatting.\n"
            "- Do NOT reorder imports, change import style, or move functions/classes around unless the user explicitly asks for it.\n"
            "- Do NOT reformat the file (no global black/flake8-style reflows, no changing quote styles, no changing indentation width).\n"
            "- Do NOT touch comments or docstrings except where strictly necessary for the requested change.\n"
            "- You MUST NOT add meta-comments like '# rest of code unchanged', '# (rest of code unchanged until run remains the same except modification below)', or similar. Those comments are forbidden.\n"
            "- When you change a function like run(), you MUST keep everything above and below that function byte-for-byte identical where possible.\n"
            "- Think in terms of 'surgical diff': change the smallest number of lines you can to satisfy the requirement, so that a human diff only shows a tiny patch, not a whole-file rewrite.\n\n"

            # =========================
            # EXECUTION BEHAVIOUR
            # =========================
            "GENERAL EXECUTION BEHAVIOUR\n"
            "- When the user gives a direct instruction to implement a change, you MUST treat it as APPROVED work.\n"
            "- Do NOT ask for permission before creating the change.\n"
            "- Perform the codemod and then show a unified diff of the change.\n"
            "- Only ask for approval BEFORE SAVING to disk if the user specifically says they want to review first.\n"
            "- If the user does NOT mention review or approval, you MUST proceed automatically.\n"
            "- When in doubt, choose action. It is better to make a small, reviewable change than to stall.\n\n"

            # =========================
            # TOOL/ANALYSIS/CODEMOD LOGIC
            # =========================
            f"{tool_mode_text}"
            "The user does NOT remember tool names. Infer which tool to use from their natural language.\n\n"

            "Your job:\n"
            " 1. Decide task_type:\n"
            "    - Use 'codemod' when the user is clearly asking to ADD, CHANGE, DELETE, or REFACTOR code or files.\n"
            "    - Use 'analysis' when the user wants a review or explanation of existing code in a specific file,\n"
            "      but is NOT asking you to change that file.\n"
            "    - Use 'tool' when tools are enabled AND the user is asking for live system actions, such as:\n"
            "         * listing files or directories (e.g. 'what's in ai/', 'ls data', 'show that folder')\n"
            "         * reading the contents of a file (e.g. 'show ai/app.py', 'open app.py', 'read that file')\n"
            "         * writing markdown notes (e.g. 'make a note', 'remember this', 'save this as a note')\n"
            "         * appending to notes (e.g. 'add this to my note about X')\n"
            "         * sending email (e.g. 'email me that note', 'send me the log as an email')\n"
            "         * checking current date/time (e.g. 'what time is it', 'what's today's date')\n"
            "    - Use 'chat' when the user is just talking / asking general questions and there is no need to\n"
            "      inspect or modify any files and no need for live system data.\n\n"

            "  2. For 'chat' tasks:\n"
            "    - Set `analysis_file` to an empty string.\n"
            "    - Set `edits` to an empty array.\n"
            "    - Set `tool` to an empty object.\n\n"

            "  3. For 'tool' tasks (only when tools are enabled):\n"
            "    - Set `analysis_file` to an empty string.\n"
            "    - Set `edits` to an empty array.\n"
            "    - Set `tool` to an object describing the tool call.\n"
            "    - Supported tool names and typical args (user will NOT say these names):\n"
            "         - 'get_current_datetime'\n"
            "             args: {} (no args needed)\n"
            "         - 'list_files'\n"
            "             args: {\"path\": \"relative/path\" (optional, default \".\"),\n"
            "                    \"recursive\": true|false (optional, default false),\n"
            "                    \"max_entries\": int (optional, default 200)}\n"
            "         - 'read_file'\n"
            "             args: {\"path\": \"relative/file.py\", \"max_chars\": 16000 (optional)}\n"
            "         - 'create_markdown_note'\n"
            "             args: {\"title\": \"short title\", \"content\": \"markdown body\"}\n"
            "         - 'append_to_markdown_note'\n"
            "             args: {\"title\": \"short title\", \"content\": \"markdown to append\"}\n"
            "         - 'send_email'\n"
            "             args: {\"to\": \"ignored here (always overridden by environment email)\",\n"
            "                    \"subject\": \"...\",\n"
            "                    \"body\": \"plain text body\",\n"
            "                    \"attachments\": [\"relative/path1\", ...] (optional)}\n"
            "    - DO NOT require the user to say the tool name. Infer it.\n\n"

            "  4. Ambiguity / clarification:\n"
            "    - If the user request is ambiguous and you cannot confidently infer the target from context, DO NOT guess a tool call.\n"
            "    - In that case, choose task_type 'chat' and ask a short clarification question.\n"
            "    - If a specific file was just mentioned and the user then says 'show it', you MAY infer that file.\n\n"

            "  5. For 'analysis' tasks (explanation):\n"
            "    - Always choose 'analysis' for questions like:\n"
            "        * 'what does this function do?'\n"
            "        * 'explain run() in that file'\n"
            "        * 'what does roi_listings.py do?'\n"
            "    - Set `analysis_file` to the requested file.\n"
            "    - Do NOT choose 'tool/read_file' for explanation questions.\n"
            "    - Chad MUST summarise behaviour in normal language (purpose, inputs, outputs, side effects).\n"
            "    - He MUST NOT dump full file contents, only small snippets if absolutely necessary.\n\n"

            "  6. For 'codemod' tasks:\n"
            "    - Leave `analysis_file` empty.\n"
            "    - Populate `edits` with concrete edits.\n"
            "    - Each edit must specify:\n"
            "        * 'file' – a RELATIVE path inside the project jail.\n"
            "        * 'operation' – one of:\n"
            "              - 'prepend_comment'          → add header comment at TOP of an existing file\n"
            "              - 'append_to_bottom'         → add new code at the BOTTOM of an existing file\n"
            "              - 'create_or_overwrite_file' → replace the ENTIRE file contents with an UPDATED version\n"
            "        * 'content' – the code to insert.\n"
            "    - When editing an existing function inside a file (like 'run()' in a Python module), you MUST keep all unrelated\n"
            "      lines (imports, comments, other functions) exactly as they are, only changing the lines strictly needed\n"
            "      around that function. No reformatting, no reordering.\n"
            "    - Do NOT use 'append_to_bottom' to define a second function with the same name or to monkey-patch.\n"
            "      Instead, you MUST keep the rest of the file exactly as it is and only change the lines needed inside that function.\n"
            "    - 'create_or_overwrite_file' is ONLY allowed when the user clearly says things like 'create a new file ...' or\n"
            "      'rewrite this file from scratch'. It MUST NOT be used for simple behavioural tweaks that could be expressed as\n"
            "      small changes to an existing function.\n"
            "    - Use 'append_to_bottom' for additions unless the user clearly requests a rewrite.\n"
            "    - ALWAYS show a unified diff after making a change, and that diff should reflect a MINIMAL modification around the\n"
            "      existing logic, not a complete replacement of the file.\n\n"

            "  7. IMPORTANT PATHS:\n"
            "    - The main Flask UI file referred to as 'app.py' lives at 'ai/app.py'. Always use that path.\n"
            "  8. Only reference files inside the project root (ENV_PROJECT_JAIL).\n"
            "  9. The output MUST be a single JSON object matching exactly this schema:\n"
            f"{json.dumps(BOB_PLAN_SCHEMA, indent=2)}\n"
            "Do not add keys or text outside of the JSON.\n\n"

            "**FAST EMAIL RULE**\n"
            "If the user mentions 'email' and a file or markdown note was just created or viewed,\n"
            "you MUST choose the 'send_email' tool and MUST attach the file automatically.\n"
            "NEVER ask for confirmation when sending emails triggered by notes.\n"
        )

        resp = openai_client.responses.create(
            model=BOB_MODEL,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            text={
                "format": {"type": "json_object"}
            },
        )

        raw = (resp.output_text or "").strip()
        first = raw.find("{")
        last = raw.rfind("}")
        if first != -1 and last != -1:
            raw = raw[first:last + 1]

        body = json.loads(raw)

        task_type = body.get("task_type", "analysis")
        summary = body.get("summary", "").strip() or user_text
        edits = body.get("edits") or []
        analysis_file = body.get("analysis_file") or ""
        tool_obj = body.get("tool") or {}

    except Exception as e:
        task_type = "analysis"
        summary = f"(STUB – OpenAI error: {e!r}) Handle user request: {user_text}"
        edits = []
        analysis_file = ""
        tool_obj = {}

    plan: dict = {
        "id": id_str,
        "date": date_str,
        "created_at": now,
        "actor": "bob",
        "kind": "plan",
        "raw_user_text": user_text,
        "task": {
            "type": task_type,
            "analysis_file": analysis_file,
            "summary": summary,
            "edits": edits,
            "tool": tool_obj,
        },
    }

    (QUEUE_DIR / f"{base}.plan.json").write_text(
        json.dumps(plan, indent=2), encoding="utf-8"
    )
    return plan


def bob_refine_codemod_with_files(
        user_text: str,
        base_task: dict,
        file_contexts: dict[str, str],
) -> dict:
    """
    Second-pass planner for codemods.

    Bob already decided task_type='codemod' and gave us a rough idea of which
    files to touch. Now we give him the *actual contents* of those files and
    ask him to produce a refined, MINIMAL codemod plan.

    He MUST:
      - Keep task_type = 'codemod'.
      - Produce 'edits' that *modify existing code* rather than inventing wrappers.
      - Respect the rules from the system prompt about not duplicating functions,
        not monkey-patching, and preserving existing logic.
      - Preserve formatting, imports, and comments everywhere except the tiny
        region that actually needs to change.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # No key = no refinement possible; just return the original task
        return base_task

    # Small safety: if there are no files, nothing to refine
    if not file_contexts:
        return base_task

    # Build a compact text block with the current contents
    files_blob_lines: list[str] = []
    for rel_path, contents in file_contexts.items():
        snippet = contents  # no truncation for now
        files_blob_lines.append(
            f"===== FILE: {rel_path} =====\n{snippet}\n"
            "===== END FILE =====\n"
        )
    files_blob = "\n".join(files_blob_lines)

    refine_prompt = (
        "You are Bob, refining your previous codemod plan now that you have the "
        "actual contents of the files from disk.\n\n"
        "The user request is:\n"
        f"{user_text}\n\n"
        "You are given the current contents of the files you are allowed to edit.\n"
        "You MUST:\n"
        "- Keep task_type = 'codemod'.\n"
        "- Preserve all existing behaviour unless the user explicitly asked for a rewrite.\n"
        "- Modify existing functions IN-PLACE instead of wrapping or duplicating them.\n"
        "- Produce the MINIMAL edits necessary to satisfy the user request.\n"
        "- Do NOT reorder imports, do NOT reformat, and do NOT touch unrelated lines.\n"
        "- For Python files where you are modifying an existing function, you may use "
        "'create_or_overwrite_file' but the updated content MUST be identical to the "
        "original everywhere except the specific few lines that implement the requested change.\n"
        "- Think like a human developer making a tiny diff, not like an auto-formatter.\n"
        f"{json.dumps(BOB_PLAN_SCHEMA, indent=2)}\n\n"
        "Return ONLY a single JSON object. Do NOT include any extra commentary.\n"
    )

    try:
        resp = openai_client.responses.create(
            model=BOB_MODEL,
            input=[
                {"role": "system", "content": refine_prompt},
                {
                    "role": "user",
                    "content": (
                        "Here are the current file contents you may edit:\n\n"
                        f"{files_blob}"
                    ),
                },
            ],
            text={"format": {"type": "json_object"}},
        )

        raw = (resp.output_text or "").strip()
        first = raw.find("{")
        last = raw.rfind("}")
        if first != -1 and last != -1:
            raw = raw[first:last + 1]

        body = json.loads(raw)

        task_type = body.get("task_type", base_task.get("type", "codemod"))
        summary = body.get("summary", base_task.get("summary", "")).strip()
        edits = body.get("edits") or []

        refined_task = {
            "type": "codemod",
            "summary": summary or base_task.get("summary", ""),
            "analysis_file": "",
            "edits": edits,
            "tool": {},
        }
        return refined_task

    except Exception as e:
        fallback = dict(base_task)
        fallback.setdefault(
            "summary",
            f"{base_task.get('summary', '')} (codemod refinement failed: {e!r})",
        )
        return fallback


def bob_simple_chat(user_text: str) -> str:
    """
    Simple Q&A mode for Bob when no file is involved.

    Used when there is no analysis_file, no edits, and no tool in the task.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return (
            "You asked: {!r}. I can't call OpenAI because OPENAI_API_KEY is "
            "not configured, but normally I'd answer this directly here."
        ).format(user_text)

    base_prompt = (
        "You are Bob, a helpful AI assistant for a developer working on the "
        "GhostFrog project.\n"
        "The user is asking a general question (no specific file needed, no live tools).\n"
        "Answer directly and concisely in plain language. Do NOT talk about "
        "plans, JSON, or tools – just reply like a normal chat assistant."
    )

    try:
        resp = openai_client.responses.create(
            model=BOB_MODEL,
            input=[
                {"role": "system", "content": base_prompt},
                {"role": "user", "content": user_text},
            ],
        )
        text = (resp.output_text or "").strip()
        return text or "I couldn't generate a detailed answer."
    except Exception as e:
        return f"I tried to answer but hit an OpenAI error: {e!r}"


def bob_answer_with_context(user_text: str, plan: dict, snippet: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "I’d like to review the file, but there is no OPENAI_API_KEY configured."

    if not snippet:
        base_prompt = (
            "The user asked you about code, but Chad could not provide the file contents.\n"
            "Answer as best you can in general terms."
        )
    else:
        base_prompt = (
            "You are Bob, reviewing code that Chad read from disk.\n"
            "The user asked a question about this file.\n\n"
            "Respond with a friendly, practical review:\n"
            "- Explain what the file appears to do.\n"
            "- Suggest concrete improvements (readability, structure, errors, etc.).\n"
            "- Keep it focused and in plain language.\n"
        )

    try:
        resp = openai_client.responses.create(
            model=BOB_MODEL,
            input=[
                {"role": "system", "content": base_prompt},
                {"role": "user", "content": f"User request:\n{user_text}"},
                {"role": "user", "content": f"File contents snippet:\n\n{snippet}"},
            ],
        )
        text = (resp.output_text or "").strip()
        return text or "I looked at the file but couldn't generate a detailed review."
    except Exception as e:
        return f"I tried to review the file but hit an OpenAI error: {e!r}"


# ---------------------------------------------------------------------------
# "Chad" – local executor layer
# ---------------------------------------------------------------------------

def _normalize_newlines(text: str) -> str:
    """
    Normalise all line endings to LF ('\n') so git diffs stay sane.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _safe_read_text(path: Path) -> str:
    """
    Read a text file as UTF-8, tolerating dodgy bytes so we don't crash
    if something is already slightly corrupted.
    """
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        data = f.read()
    return _normalize_newlines(data)


def _contains_suspicious_control_chars(text: str) -> bool:
    """
    Return True if text contains ASCII control characters other than
    newline, carriage-return, or tab.

    This is a safety net to avoid writing corrupted binary junk
    (e.g. mangled emoji bytes) into source files.
    """
    for ch in text:
        if ord(ch) < 32 and ch not in ("\n", "\r", "\t"):
            return True
    return False

def _strip_suspicious_control_chars(text: str) -> str:
    return text.replace("\x0b", "").replace("\x0c", "")

def __strip_suspicious_control_chars(text: str) -> str:
    """
    Remove ASCII control characters other than newline, carriage-return, or tab.

    This lets Chad accept LLM output that accidentally contains things like
    vertical-tab or form-feed by stripping them instead of skipping the edit.
    """
    cleaned_chars: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 32 and ch not in ("\n", "\r", "\t"):
            # strip the suspicious control char
            continue
        cleaned_chars.append(ch)
    return "".join(cleaned_chars)


def _safe_write_text(path: Path, text: str) -> None:
    """
    Write UTF-8 text with LF newlines only.

    - keeps encoding consistent
    - keeps line endings consistent
    - avoids BOMs
    """
    if not isinstance(text, str):
        text = str(text)

    text = _normalize_newlines(text)
    path.parent.mkdir(parents=True, exist_ok=True)

    # newline='\n' forces LF on disk
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _detect_comment_prefix(path: Path) -> str:
    """
    Very simple comment-style detector based on file extension.
    Used for the 'prepend_comment' operation.
    """
    ext = path.suffix.lower()
    if ext in {".py", ".sh"}:
        return "# "
    if ext in {".js", ".ts", ".jsx", ".tsx", ".c", ".cpp", ".h"}:
        return "// "
    if ext in {".php"}:
        return "// "
    return "# "


def _resolve_in_project_jail(relative_path: str) -> Path | None:
    """
    Resolve a relative path against PROJECT_ROOT, enforcing the jail.
    Returns a Path or None if the path escapes the jail.
    """
    if not relative_path:
        relative_path = "."
    target = (PROJECT_ROOT / relative_path).resolve()
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    return target


def _slugify_for_markdown(title: str) -> str:
    """
    Crude slugifier for markdown note filenames.
    """
    base = "".join(
        ch.lower() if ch.isalnum() else "-"
        for ch in (title or "").strip()
    ).strip("-")
    if not base:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"note-{timestamp}"
    while "--" in base:
        base = base.replace("--", "-")
    return base


def chad_execute_plan(id_str: str, date_str: str, base: str, plan: dict) -> dict:
    """
    Chad executes Bob's plan.

    Rules (Medium Ghost):
      - Only acts on task.type == 'codemod' for file edits.
      - For 'tool', runs a local tool (e.g. system datetime, list_files, SMTP)
      - For 'analysis', reads a file for Bob to review.
      - Only edits files INSIDE PROJECT_ROOT (ENV_PROJECT_JAIL).
      - Only edits files that ALREADY EXIST on disk.
      - No confirmation prompts – source control is the safety net.
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    task = plan.get("task") or {}
    task_type = task.get("type", "analysis")
    edits = task.get("edits") or []
    tool_obj = task.get("tool") or {}

    touched: list[str] = []

    # ------------------------------------------------------------------
    # TOOL branch – use local system capabilities (e.g. datetime, FS, SMTP)
    # ------------------------------------------------------------------
    if task_type == "tool":
        tool_name = tool_obj.get("name") or ""
        tool_args = tool_obj.get("args") or {}
        tool_result = ""
        message = ""

        if tool_name == "get_current_datetime":
            now_local = datetime.now().astimezone()
            dt_str = now_local.strftime("%A, %d %B %Y, %H:%M:%S %Z (%z)")
            tool_result = f"Local system date and time: {dt_str}"
            message = "Chad ran tool 'get_current_datetime' using the system clock."

        elif tool_name == "list_files":
            rel_path = str(tool_args.get("path") or ".")
            recursive = bool(tool_args.get("recursive", False))
            try:
                max_entries = int(tool_args.get("max_entries", 200))
            except (TypeError, ValueError):
                max_entries = 200

            base_path = _resolve_in_project_jail(rel_path)
            if base_path is None or not base_path.exists():
                message = (
                    f"Chad tried to list_files at {rel_path!r} but the path was invalid "
                    "or outside the project jail."
                )
                tool_result = ""
            else:
                entries = []
                count = 0

                if recursive and base_path.is_dir():
                    for path in base_path.rglob("*"):
                        if count >= max_entries:
                            break
                        try:
                            rel = str(path.relative_to(PROJECT_ROOT))
                        except ValueError:
                            continue
                        if path.is_dir():
                            entries.append(
                                {"path": rel, "type": "dir", "size": None}
                            )
                        else:
                            try:
                                size = path.stat().st_size
                            except OSError:
                                size = None
                            entries.append(
                                {"path": rel, "type": "file", "size": size}
                            )
                        count += 1
                elif base_path.is_dir():
                    for path in sorted(base_path.iterdir()):
                        if count >= max_entries:
                            break
                        try:
                            rel = str(path.relative_to(PROJECT_ROOT))
                        except ValueError:
                            continue
                        if path.is_dir():
                            entries.append(
                                {"path": rel, "type": "dir", "size": None}
                            )
                        else:
                            try:
                                size = path.stat().st_size
                            except OSError:
                                size = None
                            entries.append(
                                {"path": rel, "type": "file", "size": size}
                            )
                        count += 1
                else:
                    try:
                        rel = str(base_path.relative_to(PROJECT_ROOT))
                    except ValueError:
                        rel = base_path.name
                    try:
                        size = base_path.stat().st_size
                    except OSError:
                        size = None
                    entries.append(
                        {"path": rel, "type": "file", "size": size}
                    )

                if entries:
                    lines = ["Path / Type / Size(bytes):"]
                    for e in entries:
                        size_str = (
                            "dir"
                            if e["type"] == "dir"
                            else (str(e["size"]) if e["size"] is not None else "?")
                        )
                        lines.append(f"- {e['path']}  [{e['type']}]  {size_str}")
                    tool_result = "\n".join(lines)
                    message = (
                        f"Chad listed up to {len(entries)} entries under {rel_path!r}."
                    )
                else:
                    message = f"Chad found no entries under {rel_path!r}."
                    tool_result = "No files or directories found."

        elif tool_name == "read_file":
            rel_path = str(
                tool_args.get("path")
                or tool_args.get("file")
                or ""
            )
            try:
                max_chars = int(tool_args.get("max_chars", 16000))
            except (TypeError, ValueError):
                max_chars = 16000

            target_path = _resolve_in_project_jail(rel_path)
            if target_path is None or not target_path.exists() or not target_path.is_file():
                message = (
                    f"Chad tried to read_file {rel_path!r} but it does not exist, "
                    "is not a file, or is outside the project jail."
                )
                tool_result = ""
            else:
                try:
                    raw = target_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    message = (
                        f"Chad tried to read_file {rel_path!r} but it is not UTF-8 text."
                    )
                    tool_result = ""
                else:
                    if len(raw) > max_chars:
                        tool_result = raw[:max_chars] + "\n\n... (truncated)"
                    else:
                        tool_result = raw
                    message = f"Chad read_file {rel_path!r} (up to {max_chars} chars)."

        elif tool_name in {"create_markdown_note", "append_to_markdown_note"}:
            title = str(tool_args.get("title") or tool_args.get("name") or "").strip()
            content = str(tool_args.get("content") or "")
            slug = _slugify_for_markdown(title or "note")
            note_path = MARKDOWN_NOTES_DIR / f"{slug}.md"

            if tool_name == "create_markdown_note":
                note_path.write_text(content, encoding="utf-8")
                tool_result = (
                    f"Created markdown note '{title or slug}' at notes/{slug}.md."
                )
                message = "Chad created a new markdown note."
            else:
                if note_path.exists():
                    with note_path.open("a", encoding="utf-8") as f:
                        if not content.endswith("\n"):
                            content_to_write = content + "\n"
                        else:
                            content_to_write = content
                        f.write(content_to_write)
                    tool_result = (
                        f"Appended to markdown note '{title or slug}' at notes/{slug}.md."
                    )
                    message = "Chad appended to an existing markdown note."
                else:
                    note_path.write_text(content, encoding="utf-8")
                    tool_result = (
                        f"Note '{title or slug}' did not exist; created notes/{slug}.md."
                    )
                    message = (
                        "Chad could not find an existing markdown note, so he created it."
                    )

        elif tool_name == "send_email":
            to_addr = (
                os.getenv("SMTP_TO")
                or os.getenv("SMTP_TEST_TO")
                or ""
            ).strip()

            subject = str(tool_args.get("subject") or "").strip()
            body = str(tool_args.get("body") or "")

            attachments_in_args = "attachments" in tool_args
            attachments = tool_args.get("attachments")
            if attachments is None:
                attachments = []

            auto_note = False
            note_path = None
            note_rel_display = None

            if not attachments and not attachments_in_args:
                latest = None
                latest_mtime = None
                for p in MARKDOWN_NOTES_DIR.glob("*.md"):
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        continue
                    if latest is None or mtime > latest_mtime:
                        latest = p
                        latest_mtime = mtime

                if latest is not None:
                    auto_note = True
                    note_path = latest
                    note_rel_display = f"notes/{latest.name}"
                    try:
                        attachment_rel = str(latest.relative_to(PROJECT_ROOT))
                    except ValueError:
                        attachment_rel = str(latest)
                    attachments = [attachment_rel]

                    if not subject:
                        subject = f"[GhostFrog] {latest.name}"

            smtp_host = os.getenv("SMTP_HOST")
            security = (os.getenv("SMTP_SECURITY") or "starttls").lower()
            port_env = os.getenv("SMTP_PORT")
            if port_env:
                smtp_port = int(port_env)
            else:
                smtp_port = 465 if security == "ssl" else 587

            smtp_user = os.getenv("SMTP_USERNAME")
            smtp_password = os.getenv("SMTP_PASSWORD")
            from_addr = os.getenv("SMTP_FROM") or smtp_user

            if not to_addr:
                message = (
                    "Chad was asked to send_email, but no SMTP_TO / SMTP_TEST_TO "
                    "address is configured in the environment."
                )
                tool_result = ""
            elif not smtp_host or not from_addr:
                message = (
                    "Chad was asked to send_email, but SMTP settings are incomplete "
                    "(need at least SMTP_HOST and SMTP_FROM or SMTP_USERNAME)."
                )
                tool_result = ""
            else:
                try:
                    if security == "ssl":
                        smtp_cls = smtplib.SMTP_SSL
                    else:
                        smtp_cls = smtplib.SMTP

                    with smtp_cls(smtp_host, smtp_port, timeout=30) as server:
                        if security == "starttls":
                            server.starttls()
                        if smtp_user and smtp_password:
                            server.login(smtp_user, smtp_password)

                        msg = EmailMessage()
                        msg["From"] = from_addr
                        msg["To"] = to_addr
                        msg["Subject"] = subject or "(no subject)"
                        msg.set_content(body or "")

                        for rel in attachments:
                            rel_str = str(rel)
                            attach_path = _resolve_in_project_jail(rel_str)
                            if attach_path is None or not attach_path.exists():
                                continue
                            mime_type, _ = mimetypes.guess_type(str(attach_path))
                            if mime_type:
                                maintype, subtype = mime_type.split("/", 1)
                            else:
                                maintype, subtype = "application", "octet-stream"
                            with attach_path.open("rb") as f:
                                data = f.read()
                            msg.add_attachment(
                                data,
                                maintype=maintype,
                                subtype=subtype,
                                filename=attach_path.name,
                            )

                        server.send_message(msg)

                    if auto_note and note_path is not None:
                        try:
                            raw = note_path.read_text(encoding="utf-8")
                        except Exception:
                            preview_body = "(could not read note content)"
                        else:
                            if len(raw) > 16000:
                                preview_body = raw[:16000] + "\n\n... (truncated)"
                            else:
                                preview_body = raw

                        display_name = note_rel_display or note_path.name
                        tool_result = (
                            f"📎 Attached file preview ({display_name}):\n"
                            f"{preview_body}"
                        )
                    else:
                        tool_result = (
                            f"Email sent to {to_addr} with subject: "
                            f"{subject or '(no subject)'}"
                        )

                    message = (
                        f"Chad sent an email to {to_addr!r} with subject {subject!r}."
                    )
                except Exception as e:
                    message = f"Chad failed to send_email due to error: {e!r}"
                    tool_result = ""

        else:
            message = (
                f"Chad was asked to run an unknown tool: {tool_name!r}. "
                "No tool was executed."
            )
            tool_result = ""

        scratch_file = SCRATCH_DIR / f"{base}.txt"
        scratch_file.write_text(
            "GhostFrog Chad tool execution\n"
            f"ID: {base}\n"
            f"Time: {now}\n"
            f"Tool name: {tool_name or '(none)'}\n"
            f"Tool args: {tool_args}\n"
            f"Tool result:\n{tool_result or '(no result)'}\n",
            encoding="utf-8",
        )

        exec_report = {
            "id": id_str,
            "date": date_str,
            "created_at": now,
            "actor": "chad",
            "kind": "exec_result",
            "status": "success",
            "touched_files": [],
            "analysis_file": None,
            "analysis_snippet": "",
            "tool_name": tool_name,
            "tool_result": tool_result,
            "message": message,
        }
        exec_path = QUEUE_DIR / f"{base}.exec.json"
        exec_path.write_text(json.dumps(exec_report, indent=2), encoding="utf-8")
        return exec_report

    # ------------------------------------------------------------------
    # ANALYSIS branch – read a file for Bob to review
    # ------------------------------------------------------------------
    if task_type != "codemod":
        analysis_file = task.get("analysis_file") or ""
        analysis_snippet = ""
        target_rel = None

        if analysis_file:
            target_path = (PROJECT_ROOT / analysis_file).resolve()
            try:
                target_path.relative_to(PROJECT_ROOT)
            except ValueError:
                target_path = None

            if target_path is not None and target_path.exists():
                raw = target_path.read_text(encoding="utf-8")
                analysis_snippet = raw[:16000]
                target_rel = str(target_path.relative_to(PROJECT_ROOT))

        scratch_file = SCRATCH_DIR / f"{base}.txt"
        scratch_file.write_text(
            "GhostFrog Chad analysis execution\n"
            f"ID: {base}\n"
            f"Time: {now}\n"
            f"Analysis file: {target_rel or '(none)'}\n",
            encoding="utf-8",
        )

        exec_report = {
            "id": id_str,
            "date": date_str,
            "created_at": now,
            "actor": "chad",
            "kind": "exec_result",
            "status": "success",
            "touched_files": [],
            "analysis_file": target_rel,
            "analysis_snippet": analysis_snippet,
            "message": (
                f"Chad fetched {target_rel} for analysis."
                if target_rel
                else "Chad performed analysis-only; no file was read."
            ),
        }
        exec_path = QUEUE_DIR / f"{base}.exec.json"
        exec_path.write_text(json.dumps(exec_report, indent=2), encoding="utf-8")
        return exec_report

    # ------------------------------------------------------------------
    # CODEMOD branch – apply file edits
    # ------------------------------------------------------------------
    edit_logs: list[dict] = []

    for edit in edits:
        file_rel = edit.get("file")
        op = edit.get("operation")
        content = edit.get("content", "")

        if not file_rel or not op:
            edit_logs.append({
                "file": file_rel or "(none)",
                "operation": op or "(none)",
                "reason": "missing file or operation in edit",
            })
            continue

        target_path = (PROJECT_ROOT / file_rel).resolve()

        try:
            target_path.relative_to(PROJECT_ROOT)
        except ValueError:
            edit_logs.append({
                "file": file_rel,
                "operation": op,
                "reason": "target path escapes project jail",
            })
            continue

        if not target_path.exists():
            edit_logs.append({
                "file": file_rel,
                "operation": op,
                "reason": "target file does not exist on disk",
            })
            continue

        original = _safe_read_text(target_path)

        if op == "create_or_overwrite_file":
            new_text = _normalize_newlines(content)

            # strip stray control chars first
            if _contains_suspicious_control_chars(new_text):
                cleaned = _strip_suspicious_control_chars(new_text)
                edit_logs.append({
                    "file": file_rel,
                    "operation": op,
                    "reason": "new content contained suspicious control characters which were stripped",
                })
                new_text = cleaned

            # Chad-level guard: strip meta-comments Bob should never add
            if target_path.suffix.lower() == ".py":
                lines = new_text.split("\n")
                filtered_lines = []
                for line in lines:
                    if "rest of code unchanged" in line:
                        # drop Bob's narration line completely
                        continue
                    filtered_lines.append(line)
                new_text = "\n".join(filtered_lines)

                # EXTRA GUARD: preserve any line that originally contained
                # non-ASCII characters (emojis, £, typographic dashes, smart quotes, etc.)
                # but where the new version of that line has lost them. This prevents
                # LLMs from 'normalising away' Unicode symbols.
                old_lines = original.split("\n")
                new_lines = new_text.split("\n")

                limit = min(len(old_lines), len(new_lines))
                for idx in range(limit):
                    old_line = old_lines[idx]
                    new_line = new_lines[idx]

                    had_unicode = any(ord(ch) > 127 for ch in old_line)
                    has_unicode_now = any(ord(ch) > 127 for ch in new_line)

                    # Special belt-and-braces for '£'
                    if "£" in old_line and "£" not in new_line:
                        new_lines[idx] = old_line
                        continue

                    if had_unicode and not has_unicode_now:
                        # restore the original line with its Unicode intact
                        new_lines[idx] = old_line

                new_text = "\n".join(new_lines)

            norm_old = _normalize_newlines(original)
            norm_new = new_text
            if norm_old == norm_new:
                edit_logs.append({
                    "file": file_rel,
                    "operation": op,
                    "reason": "new content is identical to existing file",
                })
                continue

            _safe_write_text(target_path, new_text)
            touched.append(str(target_path.relative_to(PROJECT_ROOT)))
            edit_logs.append({
                "file": file_rel,
                "operation": op,
                "reason": "file overwritten with new content",
            })

        elif op == "append_to_bottom":
            new_text_raw = original.rstrip() + "\n\n" + content + "\n"
            new_text = _normalize_newlines(new_text_raw)

            if _contains_suspicious_control_chars(new_text):
                cleaned = _strip_suspicious_control_chars(new_text)
                edit_logs.append({
                    "file": file_rel,
                    "operation": op,
                    "reason": "resulting content contained suspicious control characters which were stripped",
                })
                new_text = cleaned

            norm_old = _normalize_newlines(original)
            norm_new = new_text
            if norm_old == norm_new:
                edit_logs.append({
                    "file": file_rel,
                    "operation": op,
                    "reason": "append produced no effective change",
                })
                continue

            _safe_write_text(target_path, new_text)
            touched.append(str(target_path.relative_to(PROJECT_ROOT)))
            edit_logs.append({
                "file": file_rel,
                "operation": op,
                "reason": "content appended to bottom of file",
            })

        elif op == "prepend_comment":
            if target_path.suffix.lower() == ".py":
                content_stripped = content.lstrip()
                if content_stripped.startswith('"""') or content_stripped.startswith("'''"):
                    lines = original.splitlines()
                    if lines and lines[0].startswith("#!"):
                        new_text_raw = (
                            lines[0]
                            + "\n\n"
                            + content
                            + "\n\n"
                            + "\n".join(lines[1:])
                        )
                    else:
                        new_text_raw = content + "\n\n" + original

                    new_text = _normalize_newlines(new_text_raw)

                    if _contains_suspicious_control_chars(new_text):
                        cleaned = _strip_suspicious_control_chars(new_text)
                        edit_logs.append({
                            "file": file_rel,
                            "operation": op,
                            "reason": "resulting content contained suspicious control characters which were stripped",
                        })
                        new_text = cleaned

                    norm_old = _normalize_newlines(original)
                    norm_new = new_text
                    if norm_old == norm_new:
                        edit_logs.append({
                            "file": file_rel,
                            "operation": op,
                            "reason": "prepend produced no effective change",
                        })
                        continue

                    _safe_write_text(target_path, new_text)
                    touched.append(str(target_path.relative_to(PROJECT_ROOT)))
                    edit_logs.append({
                        "file": file_rel,
                        "operation": op,
                        "reason": "docstring-style block prepended to Python file",
                    })
                    continue

            prefix = _detect_comment_prefix(target_path)
            new_text_raw = f"{prefix}{content}\n\n{original}"
            new_text = _normalize_newlines(new_text_raw)

            if _contains_suspicious_control_chars(new_text):
                cleaned = _strip_suspicious_control_chars(new_text)
                edit_logs.append({
                    "file": file_rel,
                    "operation": op,
                    "reason": "resulting content contained suspicious control characters which were stripped",
                })
                new_text = cleaned

            norm_old = _normalize_newlines(original)
            norm_new = new_text
            if norm_old == norm_new:
                edit_logs.append({
                    "file": file_rel,
                    "operation": op,
                    "reason": "prepend produced no effective change",
                })
                continue

            _safe_write_text(target_path, new_text)
            touched.append(str(target_path.relative_to(PROJECT_ROOT)))
            edit_logs.append({
                "file": file_rel,
                "operation": op,
                "reason": "comment line prepended to file",
            })

        else:
            edit_logs.append({
                "file": file_rel,
                "operation": op,
                "reason": f"unknown operation {op!r}",
            })

    scratch_file = SCRATCH_DIR / f"{base}.txt"
    scratch_file.write_text(
        "GhostFrog Chad execution\n"
        f"ID: {base}\n"
        f"Time: {now}\n"
        "Touched files:\n"
        + ("\n".join(touched) if touched else "(none)")
        + "\n\nEdit logs:\n"
        + (json.dumps(edit_logs, indent=2) if edit_logs else "(none)")
        + "\n",
        encoding="utf-8",
    )

    if touched:
        message = "Chad executed Bob's plan and modified files."
    else:
        if edits:
            message = (
                "Chad saw edits in the plan but skipped all of them; "
                "check edit_logs in the exec report for reasons."
            )
        else:
            message = "Chad did not modify any files (no edits in plan)."

    exec_report: dict = {
        "id": id_str,
        "date": date_str,
        "created_at": now,
        "actor": "chad",
        "kind": "exec_result",
        "status": "success",
        "touched_files": touched,
        "edits_requested": len(edits),
        "edit_logs": edit_logs,
        "message": message,
    }

    exec_path = QUEUE_DIR / f"{base}.exec.json"
    exec_path.write_text(json.dumps(exec_report, indent=2), encoding="utf-8")
    return exec_report


# ---------------------------------------------------------------------------
# Flask app + routes
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route("/chat", methods=["GET"])
def chat_page():
    """
    Serve the main chat UI from chat_ui.html (sibling of this file).
    """
    if CHAT_TEMPLATE_PATH.exists():
        html = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")
    else:
        html = "<h1>GhostFrog Bob/Chad UI</h1><p>chat_ui.html is missing.</p>"
    return render_template_string(html)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    One round-trip for:
      user → Bob(plan) → Chad(exec) → Bob(summary).

    Bob and Chad are currently synchronous.
    """
    data = request.get_json(silent=True) or {}
    raw_message = (data.get("message") or "").strip()

    if not raw_message:
        return jsonify({
            "messages": [{"role": "bob", "text": "I didn’t receive any command."}]
        })

    message = raw_message
    tools_enabled = True
    prefix = "#bob no-tools"
    if message.lower().startswith(prefix):
        tools_enabled = False
        message = message[len(prefix):].lstrip()

    if not message:
        return jsonify({
            "messages": [{"role": "bob", "text": "I didn’t receive any command."}]
        })

    id_str, date_str, base = next_message_id()

    user_path = QUEUE_DIR / f"{base}.user.txt"
    user_path.write_text(message + "\n", encoding="utf-8")

    plan = bob_build_plan(id_str, date_str, base, message, tools_enabled=tools_enabled)

    task = plan.get("task") or {}
    if task.get("type") == "codemod":
        original_edits = task.get("edits") or []
        files_for_context: set[str] = set()

        for e in original_edits:
            rel = e.get("file")
            if rel:
                files_for_context.add(rel)

        file_contexts: dict[str, str] = {}
        for rel in files_for_context:
            target = (PROJECT_ROOT / rel).resolve()
            try:
                target.relative_to(PROJECT_ROOT)
            except ValueError:
                continue
            if not target.exists() or not target.is_file():
                continue
            try:
                raw = target.read_text(encoding="utf-8")
            except Exception:
                continue
            file_contexts[rel] = raw

        if file_contexts:
            refined_task = bob_refine_codemod_with_files(
                user_text=message,
                base_task=task,
                file_contexts=file_contexts,
            )
            plan["task"] = refined_task

    exec_report = chad_execute_plan(id_str, date_str, base, plan)

    task = plan.get("task") or {}
    task_type = task.get("type", "analysis")
    summary = task.get("summary", message)
    analysis_file = task.get("analysis_file") or ""
    edits = task.get("edits") or []
    tool_obj = task.get("tool") or {}

    ui_messages = [
        {"role": "bob", "text": f"Bob: thinking… (id {base})"},
        {"role": "bob", "text": f"Bob: Plan → {summary}"},
    ]

    if task_type == "tool":
        tool_name = tool_obj.get("name") or exec_report.get("tool_name") or ""
        tool_result = exec_report.get("tool_result", "")
        tool_message = exec_report.get("message", "Chad ran a tool.")

        if tool_name == "send_email" and tool_result:
            ui_messages.append({
                "role": "bob",
                "text": tool_result,
            })
            ui_messages.append({
                "role": "chad",
                "text": tool_message,
            })
        else:
            ui_messages.append({
                "role": "chad",
                "text": tool_message,
            })

            if tool_result:
                ui_messages.append({
                    "role": "bob",
                    "text": tool_result,
                })
            else:
                ui_messages.append({
                    "role": "bob",
                    "text": "The tool did not return any result.",
                })

        ui_messages.append({
            "role": "bob",
            "text": (
                "Bob: Done (tool).\n"
                f"Tool: {tool_name or '(none)'}\n"
                f"Files created:\n"
                f"  - data/queue/{base}.user.txt\n"
                f"  - data/queue/{base}.plan.json\n"
                f"  - data/queue/{base}.exec.json\n"
                f"Scratch note: data/scratch/{base}.txt"
            ),
        })

    elif not analysis_file and not edits and not tool_obj:
        answer = bob_simple_chat(message)
        ui_messages.append({"role": "bob", "text": answer})

        ui_messages.append({
            "role": "bob",
            "text": (
                "Bob: Done (chat-only).\n"
                f"Files created:\n"
                f"  - data/queue/{base}.user.txt\n"
                f"  - data/queue/{base}.plan.json\n"
                f"  - data/queue/{base}.exec.json\n"
                f"Scratch note: data/scratch/{base}.txt"
            ),
        })

    elif task_type == "analysis":
        ui_messages.append({
            "role": "chad",
            "text": exec_report.get("message", "Chad fetched file for Bob.")
        })

        snippet = exec_report.get("analysis_snippet", "")
        review = bob_answer_with_context(message, plan, snippet)

        ui_messages.append({"role": "bob", "text": review})

        ui_messages.append({
            "role": "bob",
            "text": (
                "Bob: Done (analysis).\n"
                f"Files created:\n"
                f"  - data/queue/{base}.user.txt\n"
                f"  - data/queue/{base}.plan.json\n"
                f"  - data/queue/{base}.exec.json\n"
                f"Scratch note: data/scratch/{base}.txt"
            ),
        })

    else:
        touched_files = exec_report.get("touched_files") or []

        ui_messages.append({"role": "chad", "text": "Chad: working on Bob's plan…"})
        ui_messages.append({
            "role": "chad",
            "text": exec_report.get("message", "Chad executed Bob's plan.")
        })

        if touched_files:
            pretty = "\n".join(f" - {f}" for f in touched_files)
            ui_messages.append({"role": "chad", "text": f"Chad edited:\n{pretty}"})

            first_rel = touched_files[0]
            try:
                target_path = (PROJECT_ROOT / first_rel).resolve()
                target_path.relative_to(PROJECT_ROOT)
                content = target_path.read_text(encoding="utf-8")
                if len(content) > 16000:
                    content_snippet = content[:16000] + "\n\n... (truncated)"
                else:
                    content_snippet = content
                ui_messages.append({
                    "role": "bob",
                    "text": f"Here is the updated {first_rel}:\n\n{content_snippet}",
                })
            except Exception:
                pass

        ui_messages.append({
            "role": "bob",
            "text": (
                "Bob: Done.\n"
                f"Files created:\n"
                f"  - data/queue/{base}.user.txt\n"
                f"  - data/queue/{base}.plan.json\n"
                f"  - data/queue/{base}.exec.json\n"
                f"Scratch note: data/scratch/{base}.txt"
            ),
        })

    return jsonify({"messages": ui_messages})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("[Bob/Chad] Web UI starting on http://127.0.0.1:8765/chat")
    app.run(host="127.0.0.1", port=8765)
