"""Application entrypoint: builds the QApplication and the main window."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from local_equs_client.config import logging as app_logging
from local_equs_client.config import settings as settings_module
from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.metadata_cache import MetadataCache
from local_equs_client.data_layer.query_controller import QueryController
from local_equs_client.data_layer.query_engine import QueryEngine
from local_equs_client.data_layer.query_planner import QueryPlanner
from local_equs_client.selection.selection_model import SelectionModel
from local_equs_client.state import db
from local_equs_client.state.dao import identity
from local_equs_client.ui.main_window import MainWindow
from local_equs_client.ui.settings_panel import FirstRunWizard

logger = logging.getLogger(__name__)


def main() -> None:
    """Boot the data layer and run the Qt event loop."""
    app_logging.configure_logging()
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
    metadata_cache = MetadataCache(library)

    planner = QueryPlanner(library)
    engine = QueryEngine()

    app = QApplication.instance() or QApplication(sys.argv)

    if settings_module.get_settings().server_url is None:
        FirstRunWizard().exec()

    controller = QueryController(selection_model, planner, engine)
    window = MainWindow(selection_model, library, metadata_cache, controller)
    window.show()
    sys.exit(app.exec())
