import os
import threading

import uvicorn

from main import bootstrap_runtime
from project_paths import get_browser_name, get_env_file_path, get_instance_name
from quick_replies import QuickReplyStore
from runtime_state import RuntimeState
from trigger_rules import KeywordReplyRuleStore, TriggerRuleStore
from ui_server import UiRuntimeController, create_app


def run():
    runtime_state = RuntimeState()
    quick_store = QuickReplyStore()
    trigger_rule_store = TriggerRuleStore()
    keyword_reply_rule_store = KeywordReplyRuleStore()
    controller = UiRuntimeController(None, runtime_state, quick_store, trigger_rule_store, keyword_reply_rule_store)

    runtime_state.update_status(
        ws_connected=False,
        bridge_online=False,
        cookie_source=os.getenv("COOKIE_SOURCE", "plugin"),
        cookie_waiting=True,
        cookie_wait_reason="startup",
        project_id=os.getenv("COOKIE_PROJECT_ID", "").strip(),
        account_hint=os.getenv("COOKIE_ACCOUNT_HINT", "").strip(),
        instance_name=get_instance_name(),
        browser_name=get_browser_name(),
        env_file=get_env_file_path().name,
    )

    def bootstrap_in_background():
        try:
            xianyu_live, bridge = bootstrap_runtime(
                runtime_state=runtime_state,
                allow_console_fallback=False,
                trigger_rule_store=trigger_rule_store,
                keyword_reply_rule_store=keyword_reply_rule_store,
            )
            controller.attach_runtime(xianyu_live, bridge=bridge)
            controller.start_runtime()
        except Exception as e:
            runtime_state.update_status(llm_last_error=str(e), cookie_waiting=False)

    threading.Thread(target=bootstrap_in_background, daemon=True).start()

    host = os.getenv("UI_HOST", "127.0.0.1")
    port = int(os.getenv("UI_PORT", "8765"))
    try:
        uvicorn.run(create_app(controller), host=host, port=port, log_level="info")
    finally:
        controller.stop_bridge()


if __name__ == "__main__":
    run()
