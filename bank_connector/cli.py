"""Composition root.

`main()` is the only place where concrete dependencies are wired together —
repositories, the Enable Banking client, the sync service, and the Flask app.
"""
import logging
import sys
import threading

from bank_connector.enable_banking import EnableBankingClient
from bank_connector.settings import (
    ACTUAL_DATA_DIR,
    CONFIG_FILE,
    HOST,
    PEM_DEFAULT,
    PORT,
    STATE_FILE,
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

    config_repo = ConfigRepository(CONFIG_FILE)
    state_repo = StateRepository(STATE_FILE)
    cfg = config_repo.load()

    eb_client = EnableBankingClient(
        application_id=cfg["application_id"],
        pem_path=cfg.get("pem_path") or PEM_DEFAULT,
        redirect_url=cfg.get("redirect_url") or default_redirect_url(),
    )
    sync_service = SyncService(
        eb_client=eb_client,
        config_repo=config_repo,
        state_repo=state_repo,
        actual_data_dir=ACTUAL_DATA_DIR,
    )

    threading.Thread(target=sync_service.scheduler_loop, daemon=True).start()

    app = create_app(
        config_repo=config_repo,
        state_repo=state_repo,
        eb_client=eb_client,
        sync_service=sync_service,
    )
    app.run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()
