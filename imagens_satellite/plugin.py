from functools import partial

from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QAction, QMenu

from .provider import SateliteCadenteProvider


class SateliteCadentePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.menu = None
        self.submenus = []

    def initGui(self):
        if self.menu is not None:
            return
        provider = SateliteCadenteProvider()
        if not QgsApplication.processingRegistry().addProvider(provider):
            provider.deleteLater()
            raise RuntimeError("O provedor Satelite Cadente ja esta registrado.")
        self.provider = provider
        main_window = self.iface.mainWindow()
        groups = (
            ("obter", "Obter imagens", "/mActionAddRasterLayer.svg", (
                ("mosaico_satellite", "Sentinel-2, Landsat, NASA HLS e mosaicos web...", "/mActionAddRasterLayer.svg"),
                ("ecostress_buscar", "NASA ECOSTRESS: consultar e baixar L2T...", "/search.svg"),
                None,
                ("cbers4a_buscar_cobertura", "CBERS-4A: consultar cenas e cobertura...", "/search.svg"),
                ("cbers4a_wpm", "CBERS-4A: baixar bandas WPM...", "/mActionAddRasterLayer.svg"),
            )),
            ("produtos", "Gerar produtos", "/mActionShowRasterCalculator.png", (
                ("produto_analitico", "Sentinel-2, Landsat e HLS: gerar produtos...", "/mActionShowRasterCalculator.png"),
                ("ecostress_lst", "NASA ECOSTRESS: preparar LST (70 m)...", "/mActionShowRasterCalculator.png"),
                ("drone_ortomosaico_indices", "Drone: \u00edndices de ortomosaico...", "/mActionShowRasterCalculator.png"),
                None,
                ("cbers4a_toa", "CBERS-4A: calibrar TOA (experimental)...", "/mActionCalculateField.svg"),
                ("cbers4a_mosaico_toa", "CBERS-4A: montar mosaico TOA (experimental)...", "/mIconRasterGroup.svg"),
            )),
        )
        try:
            self.menu = QMenu(main_window)
            self.menu.setTitle("Satelite Cadente")
            self.menu.setObjectName("imagens_satellite_menu")
            for key, title, theme_icon, items in groups:
                submenu = QMenu(title, self.menu)
                self.submenus.append(submenu)
                submenu.setObjectName("imagens_satellite_menu_" + key)
                submenu.setIcon(QgsApplication.getThemeIcon(theme_icon))
                submenu.menuAction().setIconVisibleInMenu(True)
                self.menu.addMenu(submenu)
                for item in items:
                    if item is None:
                        submenu.addSeparator()
                        continue
                    name, label, theme_icon = item
                    action = QAction(QgsApplication.getThemeIcon(theme_icon), label, submenu)
                    action.setObjectName("imagens_satellite_action_" + name)
                    action.setData("imagens_satellite:" + name)
                    action.setToolTip("Abrir par\u00e2metros: " + label.rstrip("."))
                    action.setStatusTip(action.toolTip())
                    action.setIconVisibleInMenu(True)
                    action.triggered.connect(partial(self.open_algorithm, action.data()))
                    submenu.addAction(action)
                    if name == "mosaico_satellite":
                        self.action = action
            self.iface.addToolBarIcon(self.action)
            anchor = self.iface.firstRightStandardMenu()
            main_window.menuBar().insertMenu(anchor.menuAction() if anchor else None, self.menu)
        except Exception:
            self.unload()
            raise

    def open_algorithm(self, algorithm_id="imagens_satellite:mosaico_satellite", checked=False):
        try:
            import processing

            processing.execAlgorithmDialog(algorithm_id)
        except Exception as exc:
            self.iface.messageBar().pushCritical("Satelite Cadente", str(exc))

    def unload(self):
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.menu is not None:
            self.iface.mainWindow().menuBar().removeAction(self.menu.menuAction())
            for submenu in self.submenus:
                submenu.clear()
                submenu.deleteLater()
            self.submenus.clear()
            self.menu.clear()
            self.menu.deleteLater()
            self.menu = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

