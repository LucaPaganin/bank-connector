"""Composition root.

`main()` is the only place where concrete dependencies are wired together —
repositories, the Enable Banking client, the sync service, and the Flask app.
"""
import logging
import os
import sys
from apscheduler.schedulers.background import BackgroundScheduler

from bank_connector.enable_banking import EnableBankingClient
from bank_connector.settings import (
    ACTUAL_DATA_DIR,
    CONFIG_FILE,
    HOST,
    PEM_DEFAULT,
    PORT,
    STATE_FILE,
    SSL_CRT_FILE,
    SSL_KEY_FILE,
    SYNC_ENABLED,
    ACTUAL_URL,
    ACTUAL_PASSWORD,
    ACTUAL_ENCRYPTION_PASSWORD,
    APPLICATION_ID,
    REDIRECT_URL,
    default_base_url,
    default_redirect_url,
)
from bank_connector.storage import ConfigRepository, StateRepository
from bank_connector.sync import SyncService
from bank_connector.web import create_app

log = logging.getLogger("connector")


def main() -> None:
    if not CONFIG_FILE.exists():
        log.error(
            "Missing %s - copy accounts.example.json and fill in your details.",
            CONFIG_FILE,
        )
        sys.exit(1)
    
    log.info(f"Open {default_base_url()} to view the gui")

    config_repo = ConfigRepository(CONFIG_FILE)
    state_repo = StateRepository(STATE_FILE)
    cfg = config_repo.load()

    # Environment values take precedence, allowing secrets to stay out of JSON.
    application_id = APPLICATION_ID or cfg["application_id"]
    eb_client = EnableBankingClient(
        application_id=application_id,
        pem_path=PEM_DEFAULT if os.getenv("BANK_CONN_PEM_PATH") else (cfg.get("pem_path") or PEM_DEFAULT),
        redirect_url=REDIRECT_URL or cfg.get("redirect_url") or default_redirect_url(),
    )
    actual_overrides = {
        key: value for key, value in {
            "url": ACTUAL_URL,
            "password": ACTUAL_PASSWORD,
            "encryption_password": ACTUAL_ENCRYPTION_PASSWORD,
        }.items() if value
    }
    sync_service = SyncService(
        eb_client=eb_client,
        config_repo=config_repo,
        state_repo=state_repo,
        actual_data_dir=ACTUAL_DATA_DIR,
        actual_overrides=actual_overrides,
    )

    scheduler = None
    if SYNC_ENABLED:
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            sync_service.run, "interval", hours=sync_service.interval_hours,
            id="bank-sync", max_instances=1, coalesce=True,
        )
        scheduler.start()
        log.info("Auto sync enabled interval_hours=%s", sync_service.interval_hours)
    else:
        log.info(
            "Auto sync disabled (set BANK_CONN_SYNC_ENABLED=1 to enable); "
            "manual POST /sync still works"
        )

    app = create_app(
        config_repo=config_repo,
        state_repo=state_repo,
        eb_client=eb_client,
        sync_service=sync_service,
    )
    # TLS is normally terminated by the reverse proxy.  Only enable Flask TLS
    # when both certificate paths are explicitly available.
    ssl_context = (
        (SSL_CRT_FILE, SSL_KEY_FILE)
        if SSL_CRT_FILE.is_file() and SSL_KEY_FILE.is_file()
        else None
    )
    app.run(host=HOST, port=PORT, ssl_context=ssl_context)


if __name__ == "__main__":
    main()
