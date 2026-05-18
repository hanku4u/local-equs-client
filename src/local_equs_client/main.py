"""Application entrypoint: builds the QApplication and the main window."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from local_equs_client.config import logging as app_logging
from local_equs_client.config import settings as settings_module
from local_equs_client.data_layer import (
    app_telemetry,
    crash_handler,
    telemetry_client,
    update_client,
)
from local_equs_client.data_layer.download_manager import DownloadManager
from local_equs_client.data_layer.http import HttpClient
from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.metadata_cache import MetadataCache
from local_equs_client.data_layer.query_cache import QueryCache
from local_equs_client.data_layer.query_controller import QueryController
from local_equs_client.data_layer.query_engine import QueryEngine
from local_equs_client.data_layer.query_planner import QueryPlanner
from local_equs_client.data_layer.update_manager import UpdateManager
from local_equs_client.selection.selection_model import SelectionModel
from local_equs_client.selection.view_controller import ViewController
from local_equs_client.state import db
from local_equs_client.state.dao import identity
from local_equs_client.ui.main_window import MainWindow
from local_equs_client.ui.settings_panel import FirstRunWizard

logger = logging.getLogger(__name__)


def main() -> None:
    """Boot the data layer and run the Qt event loop."""
    app_logging.configure_logging()
    crash_handler.install()
    settings = settings_module.get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    conn = db.connect()
    db.migrate(conn)

    cid = identity.client_id(conn)
    logger.info("Client id: %s", cid)

    library = LocalLibrary(settings.data_dir, conn)
    indexed = library.scan()
    logger.info("Local Library scan: %d parquet files indexed", indexed)

    selection_model = SelectionModel()
    view_controller = ViewController()
    query_cache = QueryCache()
    engine = QueryEngine(cache=query_cache)

    app = QApplication.instance() or QApplication(sys.argv)

    if settings_module.get_settings().server_url is None:
        FirstRunWizard().exec()

    settings = settings_module.get_settings()
    http_client: HttpClient | None = None
    update_manager: UpdateManager | None = None
    download_manager: DownloadManager | None = None
    if settings.server_url:
        http_client = HttpClient(settings.server_url, cid)
        update_manager = UpdateManager(http_client, conn, library=library)
        download_manager = DownloadManager(http_client, library)

    metadata_cache = MetadataCache(library, conn=conn, http=http_client)
    planner = QueryPlanner(library, metadata_cache=metadata_cache)

    controller = QueryController(
        selection_model,
        planner,
        engine,
        view_controller=view_controller,
    )
    window = MainWindow(
        selection_model,
        library,
        metadata_cache,
        controller,
        update_manager=update_manager,
        download_manager=download_manager,
        view_controller=view_controller,
        conn=conn,
    )

    # C5.12: app-lifecycle telemetry. Only register a Telemetry client when
    # we have a server URL — otherwise event() / flush() stay no-ops.
    flush_timer: QTimer | None = None
    if http_client is not None:
        telemetry = telemetry_client.Telemetry(conn, http_client)
        telemetry_client.set_client(telemetry)
        telemetry_client.event("app_start", **app_telemetry.app_start_payload(conn))
        flush_timer = QTimer()
        flush_timer.setInterval(60_000)
        flush_timer.timeout.connect(telemetry_client.flush)
        flush_timer.start()
        # Flush once early so short debug sessions don't lose app_start.
        QTimer.singleShot(5_000, telemetry_client.flush)

    # C6.4: auto-updater poll on the user's configured cadence.
    update_timer: QTimer | None = None
    if http_client is not None:
        updater = update_client.UpdateClient(http_client)
        freq_hours = settings_module.get_settings().update_check_frequency_hours
        if freq_hours > 0:
            update_timer = QTimer()
            update_timer.setInterval(freq_hours * 3600 * 1000)
            update_timer.timeout.connect(
                lambda: _prompt_if_update_available(window, updater)
            )
            update_timer.start()
            # First check fires 30 s after launch so the UI settles before
            # any prompt appears.
            QTimer.singleShot(
                30_000, lambda: _prompt_if_update_available(window, updater)
            )

    def _on_quit() -> None:
        if flush_timer is not None:
            flush_timer.stop()
        if update_timer is not None:
            update_timer.stop()
        telemetry_client.event("app_exit", **app_telemetry.app_exit_payload())
        app_telemetry.record_exit(conn)
        telemetry_client.flush()
        telemetry_client.set_client(None)

    app.aboutToQuit.connect(_on_quit)

    window.show()
    sys.exit(app.exec())


def _prompt_if_update_available(
    parent: object, updater: update_client.UpdateClient
) -> None:
    """Background-check for an update and prompt the user via QMessageBox."""
    from PySide6.QtWidgets import QMessageBox, QWidget

    available = updater.check_for_update()
    if available is None:
        return

    parent_widget = parent if isinstance(parent, QWidget) else None
    body = f"Local EQUS {available.version} is available."
    if available.release_notes:
        body += f"\n\n{available.release_notes}"
    body += "\n\nDownload now? You'll be prompted to install when ready."
    choice = QMessageBox.question(
        parent_widget,
        "Update available",
        body,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if choice != QMessageBox.StandardButton.Yes:
        return

    try:
        out_path = updater.download(available)
    except Exception as exc:  # noqa: BLE001 — surface anything that goes wrong
        logger.warning("Update download failed: %s", exc)
        QMessageBox.warning(parent_widget, "Update failed", str(exc))
        return

    QMessageBox.information(
        parent_widget,
        "Update downloaded",
        f"Saved {out_path}.\n\nThe installer will launch in a later release.",
    )


if __name__ == "__main__":
    main()
