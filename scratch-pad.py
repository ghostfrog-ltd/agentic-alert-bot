'''
from infrastructure.utils.emailer import send_email
send_email("Test Alert", "This is a test from GhostFrog Bot")
'''

from infrastructure.db.migrations import apply_migrations
apply_migrations()