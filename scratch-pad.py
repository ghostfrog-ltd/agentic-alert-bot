#from agent.actions.maintenance.rebuild_model_keys import rebuild_model_keys as model_keys
#model_keys()

#from agent.reminders.reem import send_reem_test_email
#send_reem_test_email()


from dotenv import load_dotenv

from agent.actions.telegram.roi_summary import build_roi_message

def main():
    msg = build_roi_message(limit=3)
    print(msg)

if __name__ == "__main__":
    main()