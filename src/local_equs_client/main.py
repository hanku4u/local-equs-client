"""Application entrypoint: builds the QApplication and the main window."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from local_equs_client.config import logging as app_logging
from local_equs_client.config import settings as settings_module
from local_equs_client.data_layer.local_library import LocalLibrary
from local_equs_client.data_layer.metadata_cache import MetadataCache
from local_equs_client.selection.selection_model import SelectionModel
from local_equs_client.state import db
from local_equs_client.ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def main() -> None:
    """Boot the data layer and run the Qt event loop."""
    app_logging.configure_logging()
    settings = settings_module.get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    conn = db.connect()
    db.migrate(conn)

    library = LocalLibrary(settings.data_dir, conn)
    indexed = library.scan()
    logger.info("Local Library scan: %d parquet files indexed", indexed)

    selection_model = SelectionModel()
    metadata_cache = MetadataCache(library)

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(selection_model, library, metadata_cache)
    window.show()
    sys.exit(app.exec())
