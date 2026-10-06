import json
from datetime import date
from pathlib import Path

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QComboBox
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsAuthMethodConfig,
    QgsContrastEnhancement,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterAuthConfig,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterCrs,
    QgsProcessingUtils,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterNumber,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingOutputRasterLayer,
    QgsProcessingOutputFile,
    QgsProcessingParameterString,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingProvider,
    QgsRasterLayer,
)

from .satellite_mosaic_qgis import (
    ANALYTIC_PRODUCTS,
    DRONE_PRODUCTS,
    BAND_PROFILES,
    ANALYTIC_DOWNLOAD_WORKERS,
    CLASSIFICATION_METHODS,
    LST_NODATA_NOTE,
    METHOD_REFERENCES,
    MULTIBAND_RGB_PREVIEW,
    MULTIBAND_METHODOLOGY,
    RGB_COMPOSITIONS,
    RGB_METHODOLOGY,
    SPECTRAL_INDICES,
    apply_rgb_preview_style,
    generate_analytic_product,
    generate_drone_product,
    search_ecostress,
    generate_ecostress_lst,
    run,
    MANIFEST_NAME,
    _apply_layer_metadata,
    MULTIBAND_SOURCES,
    CLOUDLESS_ZOOMS,
    CLOUDLESS_YEARS,
    ESRI_ZOOMS,
    GOOGLE_ZOOMS,
    BING_ZOOMS,
)


SOURCE_VALUES = (
    "cloudless", "l2a", "esri", "google", "bing",
    "landsat45", "landsat7", "landsat89",
    "hls_l30", "hls_s30",
)
SOURCE_LABELS = (
    "Sentinel-2 Cloudless (sem API)",
    "Sentinel-2 L2A multibanda (geoprocessamento)",
    "Esri World Imagery (API key)",
    "Google Satellite (XYZ público)",
    "Bing Virtual Earth (XYZ público)",
    "Landsat 4-5 TM multibanda (USGS)",
    "Landsat 7 ETM+ multibanda (SLC-on mais limpo)",
    "Landsat 8-9 OLI/TIRS multibanda (USGS)",
    "NASA HLS v2 L30 - Landsat harmonizado (30 m; desde 2020)",
    "NASA HLS v2 S30 - Sentinel-2 harmonizado (30 m; desde 2020)",
)
ANALYTIC_SOURCE_VALUES = ("l2a", "landsat45", "landsat7", "landsat89", "hls_l30", "hls_s30")
ANALYTIC_SOURCE_LABELS = tuple(SOURCE_LABELS[SOURCE_VALUES.index(source)] for source in ANALYTIC_SOURCE_VALUES)
ANALYTIC_PRODUCT_LABELS = {
    "rgb": "Nenhum (Apenas Mosaico RGB Padrão)",
    "ndvi": "NDVI (Índice de Vegetação)",
    "ndwi": "NDWI (Índice de Água)",
    "ndbi": "NDBI (Área Construída)",
    "savi": "SAVI (Vegetação com correção da influência do solo)",
    "evi2": "EVI2 (Vegetação; duas bandas calibradas)",
    "composition": "Composições Coloridas RGB Específicas",
    "lst": "Temperatura de Superfície (LST - Landsat TIRS)",
    "lst_fused": "LST fusionada com vegetação (TsHARP - estimativa a 30 m)",
}
COMPOSITION_LABELS = {
    "natural": "RGB natural (Vermelho, Verde, Azul)",
    "vegetation": "Falsa cor - vegetação (NIR, Vermelho, Verde)",
    "urban": "Falsa cor - áreas construídas (SWIR1, NIR, Vermelho)",
}
DRONE_PRODUCT_LABELS = {
    "vari": "VARI (RGB; indicador relativo, nao substitui NDVI)",
    "ndvi": "NDVI (requer vermelho + NIR calibrados)",
    "savi": "SAVI (requer vermelho + NIR calibrados)",
    "ndwi": "NDWI (requer verde + NIR calibrados)",
}


def _product_values(source):
    return tuple(product for product in ANALYTIC_PRODUCTS
                 if product not in {"lst", "lst_fused"} or "lst" in SPECTRAL_INDICES.get(source, ()))


def _self_check():
    assert _product_values("l2a")[-1] == "evi2"
    assert _product_values("landsat89")[-1] == "evi2"
    assert RGB_COMPOSITIONS["vegetation"] == ("nir", "red", "green")
    assert "landsat89" in MULTIBAND_RGB_PREVIEW


def _apply_stretch(layer):
    if isinstance(layer, QgsRasterLayer):
        layer.setContrastEnhancement(
            QgsContrastEnhancement.ContrastEnhancementAlgorithm.StretchToMinimumMaximum,
            Qgis.RasterRangeLimit.MinimumMaximum,
        )
        layer.triggerRepaint()


class ContrastPostProcessor(QgsProcessingLayerPostProcessorInterface):
    def postProcessLayer(self, layer, context, feedback):
        _apply_stretch(layer)


class RGBPostProcessor(QgsProcessingLayerPostProcessorInterface):
    def postProcessLayer(self, layer, context, feedback):
        apply_rgb_preview_style(layer)


class ClassifiedPostProcessor(QgsProcessingLayerPostProcessorInterface):
    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsRasterLayer):
            return
        style = Path(layer.source()).with_suffix(".qml")
        message, loaded = layer.loadNamedStyle(str(style)) if style.is_file() else ("QML ausente", False)
        if not loaded:
            feedback.pushWarning(f"Estilo classificado nao carregado: {message}. Carregue o QML junto ao GeoTIFF.")
            return
        _apply_layer_metadata(layer)
        layer.triggerRepaint()
        project, layer_id = context.project(), layer.id()
        if project is not None:
            def refresh_legend():
                if sip.isdeleted(project):
                    return
                node = project.layerTreeRoot().findLayer(layer_id)
                if node is not None:
                    node.setExpanded(True)
                    parent = node.parent()
                    while parent is not None:
                        parent.setExpanded(True)
                        parent = parent.parent()
                    from qgis.utils import iface
                    if iface is not None:
                        iface.layerTreeView().refreshLayerSymbology(layer_id)
            QTimer.singleShot(0, refresh_legend)


class BandsPostProcessor(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, folder, stretch):
        super().__init__()
        self.folder = Path(folder)
        self.stretch = stretch

    def postProcessLayer(self, layer, context, feedback):
        apply_rgb_preview_style(layer)
        if self.stretch:
            _apply_stretch(layer)
        _apply_layer_metadata(layer)
        project = context.project()
        if project is None:
            return
        manifest = json.loads((self.folder / MANIFEST_NAME).read_text(encoding="utf-8"))
        root = project.layerTreeRoot()
        group = root.insertGroup(0, f"{layer.name()} - {self.folder.name}")
        node = root.findLayer(layer.id())
        if node is not None:
            group.addChildNode(node.clone())
            root.removeChildNode(node)
        for key, value in manifest["bands"].items():
            band = QgsRasterLayer(str(self.folder / value["file"]), f"{key} - {value['resolution']} m")
            if not band.isValid():
                feedback.pushWarning(f"Banda invalida para visualizacao: {key}")
                continue
            _apply_layer_metadata(band)
            if self.stretch:
                _apply_stretch(band)
            project.addMapLayer(band, False)
            group.addLayer(band).setItemVisibilityChecked(False)


def _register_output(context, requested, actual, postprocessor=None):
    """Move a solicitacao original; nao abre saidas que o usuario desmarcou."""
    layers = dict(context.layersToLoadOnCompletion())
    key = next((key for key in layers if Path(key).resolve() == Path(requested).resolve()), None)
    if key is None:
        return
    details = layers.pop(key)
    context.setLayersToLoadOnCompletion(layers)
    context.addLayerToLoadOnCompletion(str(actual), details)
    if postprocessor is not None:
        # O C++ possui o objeto; a referencia Python preserva os metodos sobrescritos.
        if not hasattr(context, "_satelite_postprocessors"):
            context._satelite_postprocessors = {}
        context._satelite_postprocessors[str(actual)] = postprocessor
        context.layerToLoadOnCompletionDetails(str(actual)).setPostProcessor(postprocessor)


def _parameters_widget(algorithm, parent):
    # Usa o formulario nativo: somente ajusta seus wrappers, sem outra classe de widget.
    try:
        from processing.gui.algorithm_widget import AlgorithmWidget
    except ImportError:
        from processing.gui.AlgorithmDialog import AlgorithmDialog as AlgorithmWidget
    widget = AlgorithmWidget(algorithm, parent=parent)
    wrappers = widget.mainWidget().wrappers
    for key, wrapper in wrappers.items():
        help_text = algorithm.parameterDefinition(key).help()
        wrapper.wrappedWidget().setToolTip(help_text)
        label = wrapper.wrappedLabel()
        if label is not None:
            label.setToolTip(help_text)

    def update(*args):
        if sip.isdeleted(widget):
            return
        source_index = int(wrappers["SOURCE"].widgetValue() or 0)
        product_step = "PRODUCT" in wrappers
        source = (ANALYTIC_SOURCE_VALUES if product_step else SOURCE_VALUES)[source_index]
        analytic = source in MULTIBAND_SOURCES
        visible = {"ZOOM": not analytic, "YEAR": analytic or source == "cloudless", "MAX_CLOUD": analytic,
                   "ESRI_AUTH": source == "esri", "DOWNLOAD_WORKERS": analytic,
                   "MONTH_START": analytic, "MONTH_END": analytic, "BAND_PROFILE": analytic}
        if product_step:
            product = ANALYTIC_PRODUCTS[int(wrappers["PRODUCT"].widgetValue() or 0)]
            classified = product not in {"rgb", "composition"}
            manual = int(wrappers["CLASSIFICATION"].widgetValue() or 0) == tuple(CLASSIFICATION_METHODS).index("manual")
            visible.update(COMPOSITION=product == "composition", CLASSIFICATION=classified,
                           SAVI_L=product == "savi",
                           FUSION_FILL_GAPS=product == "lst_fused", FUSION_MIN_R2=product == "lst_fused",
                           CLASS_COUNT=classified and not manual, CLASS_LIMITS=classified and manual)
            thermal = product in {"lst", "lst_fused"}
            fixed = thermal and bool(wrappers["THERMAL_1C"].widgetValue()) if "THERMAL_1C" in wrappers else False
            visible.update(THERMAL_1C=thermal, CLASSIFICATION=classified and not fixed,
                           CLASS_COUNT=classified and not manual and not fixed,
                           CLASS_LIMITS=classified and manual and not fixed)
        for key, show in visible.items():
            if key in wrappers:
                wrappers[key].wrappedWidget().setVisible(show)
                label = wrappers[key].wrappedLabel()
                if label is not None:
                    label.setVisible(show)
        if "ZOOM" in wrappers:
            combo = wrappers["ZOOM"].wrappedWidget()
            allowed = (0, *{"cloudless": CLOUDLESS_ZOOMS, "esri": ESRI_ZOOMS, "google": GOOGLE_ZOOMS, "bing": BING_ZOOMS}.get(source, ()))
            if isinstance(combo, QComboBox):
                for level in range(21):
                    combo.view().setRowHidden(level, level not in allowed)
                    if hasattr(combo.model(), "item"):
                        combo.model().item(level).setEnabled(level in allowed)
                if int(wrappers["ZOOM"].widgetValue() or 0) not in allowed:
                    wrappers["ZOOM"].setWidgetValue(0, widget.mainWidget().processing_context)
        if product_step:
            combo = wrappers["PRODUCT"].wrappedWidget()
            if isinstance(combo, QComboBox):
                for value in ANALYTIC_PRODUCTS:
                    position = ANALYTIC_PRODUCTS.index(value)
                    allowed = value in _product_values(source)
                    combo.view().setRowHidden(position, not allowed)
                    if hasattr(combo.model(), "item"):
                        combo.model().item(position).setEnabled(allowed)
                if product not in _product_values(source):
                    wrappers["PRODUCT"].setWidgetValue(0, widget.mainWidget().processing_context)
        else:
            years = MULTIBAND_SOURCES[source]["years"] if analytic else CLOUDLESS_YEARS if source == "cloudless" else ()
            if years:
                label = wrappers["YEAR"].wrappedLabel()
                if label is not None:
                    label.setText(f"Ano (0 = automatico; {min(years)} a {max(years)})")
            if not analytic:
                label = wrappers["ZOOM"].wrappedLabel()
                if label is not None:
                    label.setText(f"Zoom (0 = melhor da fonte; {min(allowed[1:])} a {max(allowed[1:])})")
        if analytic:
            config = MULTIBAND_SOURCES[source]
            description = f"{config['title']}: bandas analiticas, DN e calibracao por asset. Grade UTM e observacao comum por pixel. {config['rights']}"
        else:
            description = (f"{SOURCE_LABELS[SOURCE_VALUES.index(source)]}: mosaico RGB para referencia visual; nao usar para indices de reflectancia. "
                           "Tiles Esri/Google/Bing possuem restricoes de redistribuicao; Cloudless exige atribuicao Copernicus/EOX.")
        wrappers["SOURCE"].wrappedWidget().setToolTip(algorithm.parameterDefinition("SOURCE").help() + "\n\n" + description)

    if "BANDS_FOLDER" in wrappers:
        def folder_changed(*args):
            if sip.isdeleted(widget):
                return
            path = Path(wrappers["BANDS_FOLDER"].widgetValue() or "") / MANIFEST_NAME
            if not path.is_file():
                return
            try:
                source = json.loads(path.read_text(encoding="utf-8")).get("source")
            except (OSError, ValueError):
                return
            if source in ANALYTIC_SOURCE_VALUES:
                wrappers["SOURCE"].setWidgetValue(ANALYTIC_SOURCE_VALUES.index(source), widget.mainWidget().processing_context)
        wrappers["BANDS_FOLDER"].widgetValueHasChanged.connect(folder_changed)

    wrappers["SOURCE"].widgetValueHasChanged.connect(update)
    if "PRODUCT" in wrappers:
        wrappers["PRODUCT"].widgetValueHasChanged.connect(update)
        wrappers["CLASSIFICATION"].widgetValueHasChanged.connect(update)
        wrappers["THERMAL_1C"].widgetValueHasChanged.connect(update)
    else:
        def finished(successful, results):
            if not successful or not results.get("OUTPUT"):
                return
            from osgeo import gdal
            with gdal.Open(str(results["OUTPUT"])) as dataset:
                folder = dataset.GetMetadataItem("SATELITE_BANDS_FOLDER")
            if folder:
                manifest = json.loads((Path(folder) / MANIFEST_NAME).read_text(encoding="utf-8"))
                source_index = ANALYTIC_SOURCE_VALUES.index(manifest["source"])
                QTimer.singleShot(100, lambda: _open_product_dialog(folder, source_index)
                                  if QgsApplication.processingRegistry().providerById("imagens_satellite") else None)
        widget.algorithmFinished.connect(finished)
    update()
    return widget


def _open_product_dialog(bands_folder, source_index):
    import processing

    processing.execAlgorithmDialog(
        "imagens_satellite:produto_analitico",
        {
            "BANDS_FOLDER": bands_folder,
            "SOURCE": source_index,
            "OUTPUT": str(Path(bands_folder) / "Produto_Analitico.tif"),
        },
    )


def _auth_value(auth_id):
    if not auth_id:
        return None
    config = QgsAuthMethodConfig()
    if not QgsApplication.authManager().loadAuthenticationConfig(auth_id, config, True):
        raise RuntimeError(f"A configuração de autenticação não pôde ser carregada: {auth_id}")
    # O cadastro recomendado guarda a API key no campo Usuário da configuração Básica.
    return config.config("username") or config.config("password")


class SateliteCadenteAlgorithm(QgsProcessingAlgorithm):
    SOURCE = "SOURCE"
    EXTENT = "EXTENT"
    ZOOM = "ZOOM"
    YEAR = "YEAR"
    MAX_CLOUD = "MAX_CLOUD"
    MONTH_START = "MONTH_START"
    MONTH_END = "MONTH_END"
    ESRI_AUTH = "ESRI_AUTH"
    STRETCH_CONTRAST = "STRETCH_CONTRAST"
    BAND_PROFILE = "BAND_PROFILE"
    DOWNLOAD_WORKERS = "DOWNLOAD_WORKERS"
    OUTPUT = "OUTPUT"

    def name(self):
        return "mosaico_satellite"

    def displayName(self):
        return "Satelite Cadente"

    def group(self):
        return "Satelite Cadente"

    def groupId(self):
        return "imagens_satellite"

    def shortHelpString(self):
        return (
            "Versao 3.0.0: NASA HLS v2 L30/S30 harmonizado a 30 m, via espelho publico desde 2020. "
            "HLS usa NIR S30 B8A, escala 0,0001 e Fmask. Nao aumenta detalhe espacial em relacao ao Sentinel 10 m; "
            "repita janelas/datas e limites iguais para series temporais. Mosaico multidata nao e serie pronta. "
            "ECOSTRESS L2T LSTE possui busca/download e preparacao LST separados; download exige Earthdata. "
            "Autoria: Raphael S. (Radkzin), https://github.com/Radkzin . Codigo GPL-3.0: https://github.com/Radkzin/satelite-cadente .\n\n"
            "Aquisicao e processamento para qualquer area com cobertura, sem caminhos fixos. "
            "Perfis de bandas reduzem downloads; indices recebem classes visuais, rotulos, QML e relatorios. "
            "O algoritmo de drone usa ortomosaicos georreferenciados: VARI para RGB e NDVI/SAVI/NDWI quando ha NIR calibrado. "
            "Recorte por poligono, CRS metrico e intervalos termicos de 1 grau estao no gerador de produtos. "
            "CBERS-4A WPM possui algoritmos separados de busca, download DN 8 m/PAN 2 m, "
            "calibracao TOA experimental e mosaico multicena. TOA nao e reflectancia de superficie; "
            "nao inclui correcao atmosferica nem mascara automatica de nuvens/sombras. "
            "LST fusionada e estimativa, nao nova medicao termica. Classificacao visual nao comprova uso do solo.\n\n"
            "Satelite Cadente é um complemento de geoprocessamento para aquisição automática, "
            "particionamento em fragmentos e processamento de imagens orbitais e mosaicos web no QGIS. "
            "A arquitetura separa produtos RGB visuais de produtos analíticos multiespectrais e termais, "
            "usando GDAL/TMS e catálogos STAC.\n\n"
            "Como usar o complemento:\n"
            "1. Escolha a fonte da imagem.\n"
            "2. Defina a extensão no menu nativo: camada, mapa de layout, favoritos, tela atual "
            "ou desenho direto na tela do mapa.\n"
            "3. Zoom aparece nas fontes RGB; Ano tambem aparece no Cloudless. Ano e filtro de nuvens "
            "aparecem para Sentinel-2 L2A e Landsat. Contraste e paralelismo ficam nos parametros avancados.\n"
            "4. Em fonte analítica, o primeiro passo baixa as bandas e prepara um RGB de inspeção. Ao concluir interativamente, "
            "abre o segundo diálogo para escolher RGB, índice, composição colorida ou LST, "
            "sem novo download.\n"
            "5. Escolha o raster de saída e clique em Executar.\n\n"
            "Fontes: Sentinel-2, Landsat 4-5 TM, Landsat 7 ETM+, Landsat 8-9 OLI/TIRS, "
            "Esri World Imagery, Google Satellite e Bing Virtual Earth. Sentinel-2/Copernicus "
            "é aberto conforme a licença aplicável, com atribuição. Landsat/USGS é domínio "
            "público, permite uso e redistribuição e solicita crédito ao USGS. Esri tem termos "
            "e restrições comerciais próprios. Tiles Google/Bing não autorizam redistribuição "
            "comercial direta sem licença; confira os termos atuais e mantenha as atribuições.\n\n"
            "O Landsat é consultado no catálogo STAC público do Microsoft Planetary Computer. "
            "O complemento obtém automaticamente um token SAS temporário; não exige conta, "
            "cadastro no USGS nem chave de API. O EarthExplorer também é gratuito, mas seu "
            "download manual exige uma conta ERS.\n\n"
            "Sentinel-2 usa Earth Search Collection 1 (sentinel-2-c1-l2a), preservando DN com "
            "escala e offset do produto. Nao usa automaticamente a colecao antiga, cujos COGs "
            "podem conter offsets ja aplicados e metadados inconsistentes.\n\n"
            "Créditos do desenvolvedor: Instagram https://www.instagram.com/raphxel.s/; "
            "e-mail raphaelss1304@Gmail.com; Lattes http://lattes.cnpq.br/8600519866441082.\n\n"
            "As fontes analíticas entregam bandas multiespectrais com DN inteiros preservados "
            "e escala/offset gravados nos metadados. No segundo passo, escolha Nenhum (RGB "
            "padrão), NDVI, NDWI, NDBI, SAVI, composição RGB específica ou, no Landsat, LST "
            "em graus Celsius.\n\n"
            f"{LST_NODATA_NOTE} Cobertura optica nao garante cobertura termica. "
            "Referencia USGS: https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature-data-gaps-due-missing-aster-ged .\n\n"
            "Zoom 0 usa a melhor resolução da fonte: Cloudless 12-14, Esri 14-19, Google 12-20 "
            "e Bing 12-19. Fontes multibanda usam a resolução do produto, sem zoom. Ano 0 usa o "
            "mais recente disponível, exceto no Landsat 7: compara 1999-2002 e usa a cena SLC-on mais limpa para "
            "evitar as faixas de dados ausentes posteriores à falha de 31/05/2003.\n\n"
            "A chave Esri pode ser gerada gratuitamente criando uma conta no ArcGIS Developer "
            "Dashboard: https://developers.arcgis.com/dashboard/. Crie no gerenciador de "
            "autenticação do QGIS uma configuração do tipo Básica e coloque a "
            "API key no campo Usuário. Isso precisa ser feito uma única vez: "
            "QgsProcessingParameterAuthConfig reutiliza a configuração no gerenciador de senhas "
            "criptografado do QGIS.\n\n"
            "As imagens web usam EPSG:3857 (WGS 84 / Pseudo-Mercator), que deforma áreas e distâncias "
            "fora do Equador. Para métricas de paisagem, áreas, distâncias ou outro geoprocessamento "
            "quantitativo, reprojete a saída para o sistema métrico oficial da zona de estudo, como "
            "SIRGAS 2000 / UTM adequado, por exemplo a Zona 24S no Nordeste brasileiro.\n\n"
            "O download usa GDAL/TMS paralelo com HTTP/2. O progresso, ETA, log e cancelamento "
            "são os controles nativos do Processing. Se a Esri devolver apenas tiles de erro, "
            "o complemento tenta níveis inferiores válidos e informa isso no log.\n\n"
            "Metodologia / Parâmetros Utilizados:\n"
            f"- {RGB_METHODOLOGY}\n"
            f"- {MULTIBAND_METHODOLOGY}\n"
            f"- NDVI: {METHOD_REFERENCES['ndvi']}\n"
            f"- NDWI: {METHOD_REFERENCES['ndwi']}\n"
            f"- NDBI: {METHOD_REFERENCES['ndbi']}\n"
            f"- SAVI: {METHOD_REFERENCES['savi']}\n"
            f"- LST: {METHOD_REFERENCES['lst']}\n"
            "- Projecao: mosaicos web usam EPSG:3857; bandas e indices usam a grade UTM da cena "
            "de referencia, com hemisferio adequado ao recorte e sem interpolar DN. "
            "Escala/offset por asset, observacao comum por pixel e cobertura real registrada no manifesto.\n\n"
            "Changelog 2.3.0: busca por meses, fator L do SAVI configuravel e reutilizacao de bandas na mesma grade. "
            "2.2.0: LST fusionada estimada por cena, original e mascara separados; parametros principais visiveis; "
            "pos-processamento Python preservado e classificacao falsa-cor carregada explicitamente. "
            "2.1.1: cobertura termica e de cada produto registrada separadamente; avisos de lacunas LST. "
            "2.1.0: indices e LST saem com classes e legenda salvas em QML, sem alterar valores; "
            "download analitico com ate 3 bandas paralelas por padrao, ajustavel entre 1 e 4; "
            "escrita nao destrutiva, piramides DEFLATE, saidas registradas corretamente; "
            "novo mosaico coerente em UTM e formulario nativo com campos/zooms por fonte. "
            "Bandas anteriores sem manifesto precisam ser baixadas novamente para calcular indices."
        )

    def initAlgorithm(self, config=None):
        source = QgsProcessingParameterEnum(self.SOURCE, "Fonte", options=list(SOURCE_LABELS), defaultValue=0)
        source.setDescription("Fonte das imagens orbitais ou do mosaico web")
        source.setHelp(
            "Escolha entre uma fonte visual RGB e uma missão multibanda. A descrição do campo "
            "é atualizada conforme a fonte selecionada. Use fontes multibanda para índices e "
            "fontes RGB apenas para interpretação visual."
        )
        self.addParameter(source)

        extent = QgsProcessingParameterExtent(self.EXTENT, "Extensão de recorte")
        extent.setDescription("Extensão de recorte no mapa")
        extent.setHelp(
            "Bounding box da área a baixar e recortar. Use camada, layout, favorito, extensão "
            "atual ou desenho no mapa; áreas grandes podem exigir muito disco e rede. A extensão "
            "e convertida para EPSG:3857 em mosaicos web e para UTM em fontes analiticas."
        )
        self.addParameter(extent)

        zoom = QgsProcessingParameterEnum(
            self.ZOOM,
            "Zoom (0 = melhor da fonte)",
            options=["0 - Melhor da fonte"] + [str(level) for level in range(1, 21)],
            defaultValue=0,
        )
        zoom.setDescription("Zoom (0 = melhor da fonte; níveis válidos aparecem por fonte)")
        zoom.setHelp(
            "Controla a resolução dos mosaicos RGB; 0 seleciona a melhor da fonte. Cloudless "
            "12-14, Esri 14-19, Google 12-20 e Bing 12-19. Produtos multibanda usam a grade "
            "do produto e não aceitam zoom. Na Esri, se o nível escolhido não tiver cobertura, "
            "o próximo nível inferior pode ser usado automaticamente."
        )
        self.addParameter(zoom)

        year = QgsProcessingParameterNumber(
            self.YEAR,
            "Ano (0 = automático)",
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=0,
            minValue=0,
            maxValue=date.today().year,
        )
        year.setDescription("Ano da imagem (0 = seleção automática segura)")
        year.setHelp(
            "Ano da composição ou das cenas. Use 0 para seleção automática. No Landsat 7, "
            "0 compara 1999-2002 e usa a cena SLC-on mais limpa para evitar as faixas posteriores à falha do "
            "sensor; escolha explicitamente 2003-2025 somente se aceitar lacunas SLC-off. "
            "Nas demais fontes analíticas, 0 busca o ano mais recente com cobertura. Em "
            "basemaps Esri/Google/Bing a data varia por local e não é controlada por este campo."
        )
        self.addParameter(year)

        for key, label, default in ((self.MONTH_START, "Mes inicial", 1), (self.MONTH_END, "Mes final", 12)):
            month = QgsProcessingParameterNumber(key, label, QgsProcessingParameterNumber.Type.Integer,
                                                 defaultValue=default, minValue=1, maxValue=12)
            month.setHelp("Sentinel-2/Landsat: limita a busca aos meses informados, inclusive, no ano escolhido. "
                          "Use a mesma janela sazonal ao comparar anos. Padrao 1 a 12; inicio <= fim. "
                          "Periodos que cruzam dezembro devem ser executados separadamente. "
                          "O mosaico ainda pode reunir varias datas dentro da janela; nao e uma cena unica.")
            self.addParameter(month)

        clouds = QgsProcessingParameterNumber(
            self.MAX_CLOUD,
            "% máximo de nuvens por tile",
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=20,
            minValue=0,
            maxValue=100,
        )
        clouds.setDescription("Máximo de nuvens na cena/tile do catálogo (%)")
        clouds.setHelp(
            "Cobertura máxima de nuvens declarada no catálogo STAC, de 0 a 100%. Use valores "
            "baixos para índices e séries temporais; filtros rigorosos podem deixar a área sem "
            "cenas. Depois do download, SCL (Sentinel-2) ou QA_PIXEL (Landsat) transforma "
            "nuvem, sombra, cirrus e neve em NoData transparente; o filtro não inventa uma "
            "observação limpa onde a cena estiver totalmente encoberta."
        )
        self.addParameter(clouds)

        workers = QgsProcessingParameterNumber(
            self.DOWNLOAD_WORKERS, "Downloads de bandas simultâneos (1 a 4)",
            QgsProcessingParameterNumber.Type.Integer, defaultValue=ANALYTIC_DOWNLOAD_WORKERS,
            minValue=1, maxValue=4,
        )
        workers.setHelp(
            "Somente Sentinel-2 L2A e Landsat. Padrão: 3 bandas independentes em paralelo por cena. "
            "Escolha 1 para economizar memória ou redes instáveis; 4 pode ajudar em conexões rápidas. "
            "Não muda a resolução, os DN, a máscara nem a ordem de escolha das cenas. Cada tarefa "
            "tem dataset GDAL próprio e 32 MB para buffers de warp, além do cache e outras estruturas. "
            "A montagem continua sequencial; aumentar este valor não garante aceleração em qualquer rede."
        )
        workers.setFlags(workers.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(workers)

        profile = QgsProcessingParameterEnum(self.BAND_PROFILE, "Bandas a baixar",
            options=["Pacote completo (inclui termal Landsat)", "Vegetacao: RGB, NDVI, NDWI e SAVI",
                     "Indices opticos: inclui NDBI e composicao urbana"], defaultValue=0)
        profile.setHelp("Perfis enxutos preservam QA e coerencia por pixel, sem baixar bandas dispensaveis. "
                        "Vegetacao nao inclui SWIR/termal; indices opticos inclui SWIR1. "
                        "Para LST/fusao use pacote completo Landsat. Sentinel nao possui termal. "
                        "Meses podem combinar datas: confira o manifesto antes de comparacoes.")
        self.addParameter(profile)

        auth = QgsProcessingParameterAuthConfig(self.ESRI_AUTH, "Autenticação Esri", None, True)
        auth.setDescription("Autenticação Esri (API key obrigatória nesta fonte)")
        auth.setHelp(
            "Configuração Básica do gerenciador de autenticação do QGIS, com a API key Esri no "
            "campo Usuário. A chave fica criptografada, é reutilizada em qualquer projeto e "
            "não é usada pelas fontes abertas."
        )
        self.addParameter(auth)

        stretch = QgsProcessingParameterBoolean(
            self.STRETCH_CONTRAST,
            "Estiramento visual de contraste (Min/Max)",
            False,
            True,
        )
        stretch.setDescription("Estiramento visual de contraste (Min/Max)")
        stretch.setHelp(
            "Aplica um realce linear de contraste apenas na camada renderizada na tela do QGIS, "
            "preservando intactos os valores numéricos brutos do GeoTIFF para análises "
            "científicas e geoprocessamento pesado."
        )
        stretch.setFlags(stretch.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(stretch)

        output = QgsProcessingParameterRasterDestination(self.OUTPUT, "Raster de saída", QgsProcessing.TEMPORARY_OUTPUT)
        output.setDescription("Raster GeoTIFF de saída")
        output.setHelp(
            "Destino do GeoTIFF recortado. Em fontes multibanda, o caminho também define a "
            "pasta que guarda as bandas. Ao concluir, abre-se o segundo passo para escolher "
            "o produto analítico sem baixar os dados novamente."
        )
        self.addParameter(output)

    def createCustomParametersWidget(self, parent=None):
        return _parameters_widget(self, parent)

    def processAlgorithm(self, parameters, context, feedback):
        details = {}
        source_index = self.parameterAsInt(parameters, self.SOURCE, context)
        if not 0 <= source_index < len(SOURCE_VALUES):
            raise RuntimeError("Fonte invalida.")
        source = SOURCE_VALUES[source_index]
        profile_index = self.parameterAsEnum(parameters, self.BAND_PROFILE, context)
        if not 0 <= profile_index < len(BAND_PROFILES):
            raise RuntimeError("Perfil de bandas invalido.")
        extent = self.parameterAsExtent(
            parameters,
            self.EXTENT,
            context,
            QgsCoordinateReferenceSystem("EPSG:3857"),
        )
        if extent.isEmpty():
            raise RuntimeError("Defina uma extensão válida para o recorte.")
        year = self.parameterAsInt(parameters, self.YEAR, context) or None
        zoom = self.parameterAsInt(parameters, self.ZOOM, context) or None
        stretch_contrast = self.parameterAsBool(parameters, self.STRETCH_CONTRAST, context)
        output = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        access_token = _auth_value(
            self.parameterAsString(parameters, self.ESRI_AUTH, context)
        ) if source == "esri" else None
        run_result = run(
            source=source,
            year=year,
            access_token=access_token,
            extent=extent,
            zoom=zoom,
            max_cloud=self.parameterAsDouble(parameters, self.MAX_CLOUD, context),
            stretch_contrast=stretch_contrast,
            spectral_index=None,
            output=output,
            feedback=feedback,
            context=context,
            result_details=details,
            download_workers=self.parameterAsInt(parameters, self.DOWNLOAD_WORKERS, context),
            month_start=self.parameterAsInt(parameters, self.MONTH_START, context),
            month_end=self.parameterAsInt(parameters, self.MONTH_END, context),
            band_profile=BAND_PROFILES[profile_index],
        )
        if run_result is None:
            return {}
        if source in MULTIBAND_RGB_PREVIEW:
            result = run_result if Path(run_result).is_file() else output
            if not Path(result).is_file():
                raise RuntimeError(f"O raster de saída não foi produzido: {result}")
            _register_output(context, output, result, BandsPostProcessor(details["bands_folder"], stretch_contrast))
        elif stretch_contrast:
            result = run_result
            _register_output(context, output, result, ContrastPostProcessor())
        else:
            result = run_result
            _register_output(context, output, result)
        return {self.OUTPUT: result}

    def createInstance(self):
        return SateliteCadenteAlgorithm()


class SateliteCadenteAnalyticProductAlgorithm(QgsProcessingAlgorithm):
    BANDS_FOLDER = "BANDS_FOLDER"
    MASK = "MASK"
    TARGET_CRS = "TARGET_CRS"
    THERMAL_1C = "THERMAL_1C"
    REPORT = "REPORT"
    SOURCE = "SOURCE"
    PRODUCT = "PRODUCT"
    SAVI_L = "SAVI_L"
    COMPOSITION = "COMPOSITION"
    CLASSIFICATION = "CLASSIFICATION"
    CLASS_COUNT = "CLASS_COUNT"
    CLASS_LIMITS = "CLASS_LIMITS"
    FUSION_FILL_GAPS = "FUSION_FILL_GAPS"
    FUSION_MIN_R2 = "FUSION_MIN_R2"
    ORIGINAL_LST = "ORIGINAL_LST"
    FUSION_MASK = "FUSION_MASK"
    FUSION_REPORT = "FUSION_REPORT"
    OUTPUT = "OUTPUT"

    def name(self):
        return "produto_analitico"

    def displayName(self):
        return "Satelite Cadente — gerar produto das bandas"

    def group(self):
        return "Satelite Cadente"

    def groupId(self):
        return "imagens_satellite"

    def shortHelpString(self):
        return (
            "Segundo passo do complemento. Escolha o produto depois que as bandas Sentinel-2 "
            "ou Landsat/HLS já foram baixadas. HLS v2 usa reflectancia harmonizada DN x 0,0001, "
            "NIR S30 B8A, Fmask e grade 30 m, sem LST. Este algoritmo trabalha somente nos GeoTIFFs da "
            "pasta informada: não consulta o catálogo STAC nem baixa as bandas novamente.\n\n"
            "Produtos: Nenhum gera o mosaico RGB padrão; NDVI, NDWI, NDBI e SAVI criam índices "
            "Float32 com NoData -9999; Composições Coloridas permite RGB natural, falsa cor de "
            "vegetação ou urbana; LST converte a banda térmica Landsat Level-2 para graus Celsius. "
            "As bandas de origem e seus valores numéricos não são alterados.\n\n"
            "LST fusionada (TsHARP adaptado, Agam et al., 2007): estima detalhes da temperatura "
            "a partir da vegetacao da mesma cena. Grade final 30 m; nao e uma medicao termica "
            "nativa a 30 m. Ajuste por cena com base 1-(1-NDVI)^0,625, holdout espacial e correcao "
            "dos residuos conservando a media de Kelvin^4, com emissividade constante. Suporte "
            "agregado: 60 m no Landsat 7; 120 m nos demais (Landsat 8-9 nativo 100 m, agregado "
            "a 120 m para alinhar com a grade de 30 m). A grade original nao e substituida.\n"
            "Agam et al.: https://doi.org/10.1016/j.rse.2006.10.006 .\n"
            "Saidas separadas: LST fusionada, LST original, mascara Byte e relatorio JSON por cena. "
            "Mascara: 0=NoData, 1=original mantida, 2=estimativa com suporte termico, "
            "3=lacuna estimada sem temperatura observada. O preenchimento e desmarcado por padrao; "
            "nao estima agua/nuvens ou pixels sem NDVI. NDVI fino fora da faixa observada nao e "
            "estimado. R2 minimo padrao 0,2; cenas recusadas conservam somente a LST original. "
            "O RMSE interno nao comprova precisao a 30 m; valide independentemente, especialmente "
            "em ambientes urbanos. Limite de fusao: 10 milhoes de pixels; divida areas maiores.\n\n"
            f"{LST_NODATA_NOTE} O log e os metadados informam a cobertura efetiva do produto. "
            "Referencia USGS: https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature-data-gaps-due-missing-aster-ged .\n\n"
            "Classificacao automatica dos indices e LST: por padrao, 5 intervalos iguais. "
            "Renderizador Banda simples-falsa-cor, com cores, limites, legenda e rampa editavel. "
            "O GeoTIFF continua Float32 e o QML de mesmo nome guarda cores discretas e legenda; "
            "mantenha os dois juntos ao transferir para outro projeto. Nao e um raster de codigos de classe.\n\n"
            "Metodologias de classificacao:\n"
            "- Intervalos iguais: divide a amplitude real em faixas de mesma largura.\n"
            "- Quantis: divide a distribuicao em quantidades aproximadamente iguais de pixels.\n"
            "- Jenks: quebras naturais, reduzindo a variacao dentro das classes.\n"
            "- Desvio-padrao: classes relativas a media e dispersao; o numero final pode variar.\n"
            "- Limites manuais: pontos de corte crescentes, separados por ponto e virgula; "
            "por exemplo 0;0,2;0,4;0,6. Os limites geram uma classe a mais.\n\n"
            "Intervalos iguais usam minimo/maximo exatos, excluindo NoData. Quantis e desvio-padrao "
            "usam ate 10000 pixels amostrados sistematicamente; Jenks usa ate 1000, por custo computacional. "
            "Empates podem reduzir o numero de classes. NoData permanece transparente.\n"
            "As classes sao numericas, nao rotulos automaticamente validados de agua, cidade ou vegetacao. "
            "Para comparacao temporal, use os mesmos limites manuais e valide-os localmente. "
            "NDVI e SAVI nao compartilham obrigatoriamente limiares; NDWI/NDBI positivos nao comprovam "
            "agua/edificacoes, e LST nao tem limite universal de ilha de calor. "
            "Os autores das equacoes abaixo nao definem uma legenda universal. "
            "Classificadores: implementacoes nativas do QGIS Development Team, "
            "https://docs.qgis.org/3.44/en/docs/user_manual/working_with_vector/vector_properties.html#graduated-renderer .\n\n"
            "Metodologia: as bandas ja recortadas sao alinhadas por vizinho mais proximo na grade "
            "UTM mais grossa exigida pelo produto. Sentinel-2 usa escala e offset do asset STAC; Landsat usa reflectancia "
            "= DN x 0,0000275 - 0,2 e LST = DN x 0,00341802 + 149 - 273,15. "
            "Referências: "
            f"{METHOD_REFERENCES['ndvi']} {METHOD_REFERENCES['ndwi']} "
            f"{METHOD_REFERENCES['ndbi']} {METHOD_REFERENCES['savi']}"
        )

    def initAlgorithm(self, config=None):
        folder = QgsProcessingParameterFile(
            self.BANDS_FOLDER,
            "Pasta das bandas já baixadas",
            behavior=QgsProcessingParameterFile.Folder,
        )
        folder.setDescription("Pasta criada no primeiro passo, contendo as bandas GeoTIFF")
        folder.setHelp(
            "Pasta de saída do download multibanda. Ela contém as bandas brutas e é reutilizada "
            "para gerar o produto escolhido localmente, sem novo download."
        )
        self.addParameter(folder)

        source = QgsProcessingParameterEnum(
            self.SOURCE,
            "Fonte das bandas baixadas",
            options=list(ANALYTIC_SOURCE_LABELS),
            defaultValue=0,
        )
        source.setDescription("Missão correspondente aos arquivos da pasta")
        source.setHelp(
            "É preenchida automaticamente após o download. Mantenha a missão que corresponde "
            "às bandas para aplicar a calibração e os nomes de bandas corretos."
        )
        self.addParameter(source)

        product = QgsProcessingParameterEnum(
            self.PRODUCT,
            "Produto a gerar",
            options=[ANALYTIC_PRODUCT_LABELS[value] for value in ANALYTIC_PRODUCTS],
            defaultValue=0,
        )
        product.setDescription("Índice, composição colorida ou mosaico RGB gerado das bandas locais")
        product.setHelp(
            "Nenhum gera RGB padrão. NDVI mede vegetação; NDWI realça água; NDBI realça áreas "
            "construídas; SAVI reduz a influência do solo; EVI2 usa NIR/vermelho em reflectância "
            "de superfície calibrada. LST só está disponível para Landsat "
            "com banda térmica. As composições alteram apenas a combinação visual de bandas. "
            "LST fusionada estima um produto separado na grade optica de 30 m, sem alterar a LST "
            "original. A fusao so aceita relacao inversa vegetacao-temperatura com validacao interna; "
            "nao comprova temperatura real a 30 m. "
            f"{LST_NODATA_NOTE}"
        )
        self.addParameter(product)

        soil = QgsProcessingParameterNumber(self.SAVI_L, "Fator de ajuste do solo (L do SAVI)",
                                            QgsProcessingParameterNumber.Type.Double,
                                            defaultValue=0.5, minValue=0, maxValue=1)
        soil.setHelp("Somente SAVI: (1+L)*(NIR-Red)/(NIR+Red+L), com reflectancia calibrada. "
                     "Padrao 0,5; L=0 equivale ao NDVI. Justifique o ajuste pela cobertura e "
                     "mantenha L fixo nas comparacoes. Aplicacaoindices.pdf, p. 4; "
                     "MET-479-Waters-et-al-SEBAL.pdf, p. 19. O valor fica registrado no GeoTIFF.")
        self.addParameter(soil)

        composition = QgsProcessingParameterEnum(
            self.COMPOSITION,
            "Tipo de composição RGB",
            options=[COMPOSITION_LABELS[value] for value in RGB_COMPOSITIONS],
            defaultValue=0,
        )
        composition.setDescription("Combinação de bandas da composição RGB")
        composition.setHelp(
            "Usado somente ao escolher Composições Coloridas RGB Específicas. RGB natural usa "
            "vermelho, verde e azul; falsa cor de vegetação usa NIR, vermelho e verde; falsa "
            "cor urbana usa SWIR1, NIR e vermelho."
        )
        self.addParameter(composition)

        fill = QgsProcessingParameterBoolean(self.FUSION_FILL_GAPS, "Estimar também as lacunas da LST (não são temperaturas observadas)", defaultValue=False)
        fill.setHelp(
            "Somente na LST fusionada. Desmarcado: preserva os buracos da LST original. Marcado: "
            "estima lacunas usando vegetação da mesma cena, somente onde há NDVI válido e modelo "
            "aceito. Não recupera medições nem elimina todas as lacunas; não estima água, nuvens ou "
            "pixels sem bandas ópticas. A máscara marca essas estimativas com código 3."
        )
        self.addParameter(fill)

        quality = QgsProcessingParameterNumber(self.FUSION_MIN_R2, "R² mínimo da validação interna da fusão", QgsProcessingParameterNumber.Type.Double,
                                             defaultValue=0.2, minValue=0, maxValue=1)
        quality.setHelp(
            "Somente na fusão. Holdout espacial na grade agregada, com pelo menos 40 células completas. "
            "Padrão: R² >= 0,2 e relação inversa vegetação-temperatura. Ajustes ruins são recusados; "
            "reduzir o limite aceita modelos piores. O RMSE informado é interno na escala agregada, "
            "não prova precisão térmica a 30 m. É necessária validação independente local."
        )
        quality.setFlags(quality.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(quality)

        classification = QgsProcessingParameterEnum(
            self.CLASSIFICATION, "Método de classificação visual do produto",
            options=list(CLASSIFICATION_METHODS.values()), defaultValue=0,
        )
        classification.setHelp(
            "Aplica cores discretas e legenda automaticamente ao NDVI, NDWI, NDBI, SAVI ou LST. "
            "Intervalos iguais usam a amplitude real; quantis equilibram pixels; Jenks agrupa valores; "
            "desvio-padrão expressa dispersão. Limites manuais permitem critérios validados e comparação "
            "temporal. O método modifica somente o estilo QML, nunca os valores Float32 do GeoTIFF."
        )
        self.addParameter(classification)

        count = QgsProcessingParameterNumber(
            self.CLASS_COUNT, "Número de classes (2 a 20)",
            QgsProcessingParameterNumber.Type.Integer, defaultValue=5, minValue=2, maxValue=20,
        )
        count.setHelp(
            "Quantidade solicitada de faixas numéricas. Empates ou baixa variabilidade podem gerar "
            "menos classes; desvio-padrão pode ajustar a quantidade. Não define tipos de cobertura da terra."
        )
        self.addParameter(count)

        limits = QgsProcessingParameterString(
            self.CLASS_LIMITS, "Limites de classe (separados por ponto e vírgula)", defaultValue="", optional=True,
        )
        limits.setHelp(
            "Somente para Limites manuais. Informe 1 a 19 pontos de corte crescentes: "
            "0;0,2;0,4;0,6 é um exemplo de sintaxe, não uma recomendação universal. Aceita vírgula "
            "ou ponto decimal. Cada limite pertence à classe inferior (<=); os extremos continuam "
            "visíveis. Para LST use graus Celsius. Justifique os limites com literatura e validação local."
        )
        self.addParameter(limits)

        fixed = QgsProcessingParameterBoolean(self.THERMAL_1C, "Temperatura: classes de 1 Celsius", defaultValue=False)
        fixed.setHelp("Somente LST e LST estimada. Substitui metodo/numero de classes por intervalos de 1 Celsius. "
                      "submissao-2752-arquivo-11261-1.pdf, p. 3. Nao reaplica conversao Level-1 a ST Level-2.")
        self.addParameter(fixed)
        mask = QgsProcessingParameterVectorLayer(self.MASK, "Limite poligonal (opcional)",
            types=[QgsProcessing.TypeVectorPolygon], optional=True)
        mask.setHelp("Recorta todos os produtos e auxiliares da fusao; exterior vira NoData. Bandas originais intactas.")
        self.addParameter(mask)
        crs = QgsProcessingParameterCrs(self.TARGET_CRS, "SRC projetado de saida (opcional)", optional=True)
        crs.setHelp("Em branco mantem SRC nativo. Escolha SRC em metros, por exemplo SIRGAS/UTM da zona local. "
                    "Vizinho mais proximo preserva classes e resolucao numerica; nao cria detalhe termico ou SWIR a 10 m.")
        self.addParameter(crs)

        output = QgsProcessingParameterRasterDestination(
            self.OUTPUT,
            "Raster GeoTIFF do produto",
            QgsProcessing.TEMPORARY_OUTPUT,
        )
        output.setDescription("GeoTIFF final do índice ou composição selecionada")
        output.setHelp(
            "Destino do produto gerado. Índices são Float32 com NoData -9999; composições RGB "
            "são GeoTIFFs de visualização sem alteração das bandas brutas de origem. Índices e LST "
            "incluem arquivo QML com classificação e legenda; mantenha-o junto ao GeoTIFF."
            " Na fusao tambem sao gravadas LST original, mascara de proveniencia e relatorio JSON, "
            "sem substituir arquivos existentes; as saidas auxiliares aparecem nos resultados."
        )
        self.addParameter(output)
        self.addOutput(QgsProcessingOutputRasterLayer(self.ORIGINAL_LST, "LST original preservada (somente na fusão)"))
        self.addOutput(QgsProcessingOutputRasterLayer(self.FUSION_MASK, "Origem dos pixels: observados/estimados (somente na fusão)"))
        self.addOutput(QgsProcessingOutputFile(self.FUSION_REPORT, "Metodologia e validação por cena (JSON; somente na fusão)"))
        self.addOutput(QgsProcessingOutputFile(self.REPORT, "Classes, areas, datas e limites de interpretacao (JSON)"))

    def processAlgorithm(self, parameters, context, feedback):
        source_index = self.parameterAsInt(parameters, self.SOURCE, context)
        product_index = self.parameterAsInt(parameters, self.PRODUCT, context)
        composition_index = self.parameterAsInt(parameters, self.COMPOSITION, context)
        if not 0 <= source_index < len(ANALYTIC_SOURCE_VALUES):
            raise RuntimeError("Fonte das bandas inválida.")
        if not 0 <= product_index < len(ANALYTIC_PRODUCTS):
            raise RuntimeError("Produto selecionado inválido.")
        classification_index = self.parameterAsInt(parameters, self.CLASSIFICATION, context)
        if not 0 <= classification_index < len(CLASSIFICATION_METHODS):
            raise RuntimeError("Metodo de classificacao invalido.")
        composition_values = tuple(RGB_COMPOSITIONS)
        if not 0 <= composition_index < len(composition_values):
            composition_index = 0
        output = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        requested_output = output
        output_path = Path(output)
        if output_path.stem == "Produto_Analitico":
            output = str(output_path.with_name(f"{output_path.stem}_{ANALYTIC_PRODUCTS[product_index]}{output_path.suffix}"))
        fusion_details = {}
        product_details = {}
        mask_layer = self.parameterAsVectorLayer(parameters, self.MASK, context)
        mask = QgsProcessingUtils.convertToCompatibleFormat(mask_layer, False, "limite_produto", ["shp"],
            "shp", context, feedback) if mask_layer is not None else None
        target = self.parameterAsCrs(parameters, self.TARGET_CRS, context)
        result = generate_analytic_product(
            ANALYTIC_SOURCE_VALUES[source_index],
            self.parameterAsFile(parameters, self.BANDS_FOLDER, context),
            ANALYTIC_PRODUCTS[product_index],
            composition_values[composition_index],
            output,
            feedback,
            classification=tuple(CLASSIFICATION_METHODS)[classification_index],
            class_count=self.parameterAsInt(parameters, self.CLASS_COUNT, context),
            manual_limits=self.parameterAsString(parameters, self.CLASS_LIMITS, context),
            fusion_fill_gaps=self.parameterAsBool(parameters, self.FUSION_FILL_GAPS, context),
            fusion_min_r2=self.parameterAsDouble(parameters, self.FUSION_MIN_R2, context),
            fusion_details=fusion_details,
            savi_l=self.parameterAsDouble(parameters, self.SAVI_L, context),
            mask=mask, target_crs=target.toWkt() if target.isValid() else None,
            thermal_interval=ANALYTIC_PRODUCTS[product_index] in {"lst", "lst_fused"} and
                self.parameterAsBool(parameters, self.THERMAL_1C, context),
            product_details=product_details,
        )
        if ANALYTIC_PRODUCTS[product_index] in {"rgb", "composition"}:
            _register_output(context, requested_output, result, RGBPostProcessor())
        else:
            _register_output(context, requested_output, result, ClassifiedPostProcessor())
        outputs = {self.OUTPUT: result}
        if product_details:
            outputs[self.REPORT] = product_details["report"]
        if fusion_details:
            outputs.update({self.ORIGINAL_LST: fusion_details["original_lst"], self.FUSION_MASK: fusion_details["provenance_mask"],
                            self.FUSION_REPORT: fusion_details["method_report"]})
        return outputs

    def createInstance(self):
        return SateliteCadenteAnalyticProductAlgorithm()

    def createCustomParametersWidget(self, parent=None):
        return _parameters_widget(self, parent)


class DroneOrthomosaicAlgorithm(QgsProcessingAlgorithm):
    def name(self):
        return "drone_ortomosaico_indices"

    def displayName(self):
        return "Drone - gerar indice de ortomosaico"

    def group(self):
        return "Satelite Cadente"

    def groupId(self):
        return "imagens_satellite"

    def shortHelpString(self):
        return (
            "Usa bandas de ortomosaicos GeoTIFF georreferenciados, em SRC projetado. Fotos soltas "
            "nao sao aceitas: processe-as antes em ODM, WebODM, Metashape ou software equivalente. "
            "VARI usa RGB e serve apenas como indicador visual relativo de vegetacao; nao e NDVI. "
            "NDVI, SAVI e NDWI exigem banda NIR de sensor multiespectral, alinhamento entre bandas "
            "e reflectancia calibrada. Para um unico GeoTIFF multibanda, selecione o mesmo raster "
            "nos campos necessarios e informe o numero de cada banda. Para arquivos separados, "
            "selecione cada raster e use banda 1. A escala e o deslocamento convertem o valor do "
            "pixel em reflectancia: use 1 e 0 para reflectancia 0-1, ou 0,0001 e 0 para valores "
            "0-10000. O complemento alinha as bandas, preserva NoData, classifica e cria rotulos/QML."
        )

    def initAlgorithm(self, config=None):
        product = QgsProcessingParameterEnum("PRODUCT", "Produto", options=[DRONE_PRODUCT_LABELS[v] for v in DRONE_PRODUCTS])
        product.setHelp("VARI requer RGB. NDVI/SAVI requerem vermelho e NIR. NDWI requer verde e NIR.")
        self.addParameter(product)
        for key, label in (("RED", "Vermelho"), ("GREEN", "Verde"), ("BLUE", "Azul"), ("NIR", "Infravermelho proximo (NIR)")):
            raster = QgsProcessingParameterRasterLayer(key, f"Ortomosaico/banda {label}", optional=True)
            raster.setHelp("Pode ser um GeoTIFF de uma banda ou o mesmo GeoTIFF multibanda usado nos outros campos.")
            self.addParameter(raster)
            band = QgsProcessingParameterNumber(f"{key}_BAND", f"Numero da banda {label}",
                QgsProcessingParameterNumber.Type.Integer, defaultValue=1, minValue=1)
            self.addParameter(band)
        scale = QgsProcessingParameterNumber("SCALE", "Fator de escala para reflectancia",
            QgsProcessingParameterNumber.Type.Double, defaultValue=1.0, minValue=0.000000001)
        scale.setHelp("Use 1 para valores 0-1 ou RGB Byte; use 0,0001 quando reflectancia estiver armazenada como 0-10000.")
        self.addParameter(scale)
        offset = QgsProcessingParameterNumber("OFFSET", "Deslocamento aditivo da reflectancia",
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0)
        self.addParameter(offset)
        soil = QgsProcessingParameterNumber("SAVI_L", "Fator L do SAVI", QgsProcessingParameterNumber.Type.Double,
            defaultValue=0.5, minValue=0, maxValue=1)
        soil.setHelp("Somente SAVI; padrao 0,5. Mantenha o valor fixo em comparacoes.")
        self.addParameter(soil)
        classification = QgsProcessingParameterEnum("CLASSIFICATION", "Metodo de classificacao visual",
            options=list(CLASSIFICATION_METHODS.values()), defaultValue=0)
        self.addParameter(classification)
        self.addParameter(QgsProcessingParameterNumber("CLASS_COUNT", "Numero de classes",
            QgsProcessingParameterNumber.Type.Integer, defaultValue=5, minValue=2, maxValue=20))
        self.addParameter(QgsProcessingParameterString("CLASS_LIMITS", "Limites manuais separados por ponto e virgula",
            defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterVectorLayer("MASK", "Limite poligonal (opcional)",
            types=[QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterCrs("TARGET_CRS", "SRC projetado de saida (opcional)", optional=True))
        self.addParameter(QgsProcessingParameterRasterDestination("OUTPUT", "Indice GeoTIFF"))
        self.addOutput(QgsProcessingOutputFile("REPORT", "Relatorio de classes e metodologia"))

    def processAlgorithm(self, parameters, context, feedback):
        product_index = self.parameterAsInt(parameters, "PRODUCT", context)
        if not 0 <= product_index < len(DRONE_PRODUCTS):
            raise RuntimeError("Produto de drone invalido.")
        product = DRONE_PRODUCTS[product_index]
        bands = {}
        for key in ("RED", "GREEN", "BLUE", "NIR"):
            layer = self.parameterAsRasterLayer(parameters, key, context)
            if layer is not None:
                bands[key.lower()] = (layer.source().split("|", 1)[0], self.parameterAsInt(parameters, f"{key}_BAND", context))
        classification_index = self.parameterAsInt(parameters, "CLASSIFICATION", context)
        if not 0 <= classification_index < len(CLASSIFICATION_METHODS):
            raise RuntimeError("Metodo de classificacao invalido.")
        layer = self.parameterAsVectorLayer(parameters, "MASK", context)
        mask = QgsProcessingUtils.convertToCompatibleFormat(layer, False, "limite_drone", ["shp"], "shp",
            context, feedback) if layer else None
        target = self.parameterAsCrs(parameters, "TARGET_CRS", context)
        details = {}
        output = generate_drone_product(bands, product, self.parameterAsOutputLayer(parameters, "OUTPUT", context),
            feedback, tuple(CLASSIFICATION_METHODS)[classification_index],
            self.parameterAsInt(parameters, "CLASS_COUNT", context),
            self.parameterAsString(parameters, "CLASS_LIMITS", context),
            self.parameterAsDouble(parameters, "SAVI_L", context),
            self.parameterAsDouble(parameters, "SCALE", context),
            self.parameterAsDouble(parameters, "OFFSET", context), mask,
            target.toWkt() if target.isValid() else None, details)
        _register_output(context, self.parameterAsOutputLayer(parameters, "OUTPUT", context), output,
                         ClassifiedPostProcessor())
        return {"OUTPUT": output, "REPORT": details["report"]}

    def createInstance(self):
        return DroneOrthomosaicAlgorithm()


class CbersCoverageAlgorithm(QgsProcessingAlgorithm):
    def name(self): return 'cbers4a_buscar_cobertura'

    def displayName(self): return 'CBERS-4A WPM - buscar cenas e cobertura (sem download)'

    def group(self): return 'Satelite Cadente'

    def groupId(self): return 'imagens_satellite'

    def shortHelpString(self):
        return ('Pesquisa todas as cenas WPM L4 catalogadas para o periodo e devolve uma selecao '
            'gulosa de cenas distintas que cobre a maior area geografica possivel. Selecione a '
            'delimitacao poligonal; o relatorio lista IDs, datas, path/row, ganhos e fracao coberta. '
            'A busca nao baixa imagens. A cobertura e apenas das pegadas, nao garante pixel valido; '
            'catalogo nao fornece mascara automatica de nuvens. Use os IDs para baixar/calibrar '
            'cenas e depois execute o mosaico multicena. Uma cobertura abaixo de 100% significa '
            'que o acervo pesquisado nao observa toda a delimitacao no periodo escolhido.')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer('MASK','Delimitacao poligonal',types=[QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterNumber('YEAR_START','Ano inicial',QgsProcessingParameterNumber.Type.Integer,defaultValue=2019,minValue=2019,maxValue=date.today().year))
        self.addParameter(QgsProcessingParameterNumber('YEAR_END','Ano final',QgsProcessingParameterNumber.Type.Integer,defaultValue=date.today().year,minValue=2019,maxValue=date.today().year))
        self.addParameter(QgsProcessingParameterFileDestination('OUTPUT','Relatorio de cobertura JSON','JSON'))
        self.addOutput(QgsProcessingOutputFile('REPORT','Relatorio e selecao das cenas'))

    def processAlgorithm(self, parameters, context, feedback):
        from .satellite_mosaic_qgis import search_cbers_wpm_coverage
        layer=self.parameterAsVectorLayer(parameters,'MASK',context)
        if layer is None or not layer.isValid(): raise RuntimeError('Delimitacao invalida.')
        geom=None;transform=QgsCoordinateTransform(layer.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),context.transformContext())
        for feature in layer.getFeatures():
            part=feature.geometry();part.transform(transform)
            shape=__import__('osgeo.ogr',fromlist=['ogr']).CreateGeometryFromJson(part.asJson())
            geom=shape if geom is None else geom.Union(shape)
        start=self.parameterAsInt(parameters,'YEAR_START',context);end=self.parameterAsInt(parameters,'YEAR_END',context)
        report=search_cbers_wpm_coverage(geom,start,end,feedback)
        path=self.parameterAsFileOutput(parameters,'OUTPUT',context)
        Path(path).write_text(json.dumps(report,indent=2),encoding='utf-8')
        feedback.pushInfo(f"Cobertura geometrica maxima do catalogo: {report['geometric_union_coverage']:.1%}; cenas selecionadas: {len(report['selected_scenes'])}.")
        if report['geometric_union_coverage']<.98: feedback.pushWarning('Catalogo sem cenas para cobrir 98% do poligono neste periodo; ampliar datas ou usar outra fonte.')
        return {'OUTPUT':path,'REPORT':path}

    def createInstance(self): return CbersCoverageAlgorithm()


class CbersWpmAlgorithm(QgsProcessingAlgorithm):
    def name(self):
        return "cbers4a_wpm"

    def displayName(self):
        return "CBERS-4A WPM gratuito - bandas DN 8 m / PAN 2 m"

    def group(self):
        return "Satelite Cadente"

    def groupId(self):
        return "imagens_satellite"

    def shortHelpString(self):
        return (
            "Busca cenas ortorretificadas WPM L4 no INPE e baixa um recorte de uma unica cena. "
            "Azul, verde, vermelho e NIR: 8 m; PAN opcional: 2 m. "
            "A lista CENAS.json permite escolher o identificador e executar novamente. "
            "Sem ID, prioriza cobertura geometrica e depois data; rejeita cobertura abaixo de 98%. "
            "ID explicito permite cobertura parcial com aviso. NAO escolhe a menos nublada: o catalogo pode "
            "nao informar nuvens. Inspecione visualmente nuvens, sombras e cobertura. "
            "DN nao e reflectancia: estes arquivos nao sao aceitos para NDVI/SAVI no gerador "
            "analitico Sentinel/Landsat. Use o algoritmo CBERS calibrar TOA para estes arquivos. "
            "Nao produz indices artificiais de 2 m nem substitui Sentinel L2A. "
            "Fonte: https://data.inpe.br/dados/cbers-4a/"
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterExtent("EXTENT", "Extensao de recorte"))
        self.addParameter(QgsProcessingParameterNumber("YEAR", "Ano", QgsProcessingParameterNumber.Type.Integer,
            defaultValue=date.today().year, minValue=2020, maxValue=date.today().year))
        self.addParameter(QgsProcessingParameterString("SCENE_ID", "ID da cena (vazio = mais recente)", optional=True))
        self.addParameter(QgsProcessingParameterBoolean("PAN", "Baixar tambem pancromatica de 2 m", defaultValue=False))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT", "Pasta de saida (nova)"))

    def processAlgorithm(self, parameters, context, feedback):
        from .satellite_mosaic_qgis import download_cbers_wpm
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
        extent = self.parameterAsExtent(parameters, "EXTENT", context, crs)
        folder = download_cbers_wpm(
            (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()),
            self.parameterAsInt(parameters, "YEAR", context),
            self.parameterAsString(parameters, "SCENE_ID", context).strip(),
            self.parameterAsBool(parameters, "PAN", context),
            self.parameterAsString(parameters, "OUTPUT", context), feedback)
        return {"OUTPUT": folder}

    def createInstance(self):
        return CbersWpmAlgorithm()


class CbersToaAlgorithm(QgsProcessingAlgorithm):
    def name(self):
        return 'cbers4a_toa'

    def displayName(self):
        return 'CBERS-4A WPM - calibrar TOA e gerar NDVI/SAVI/NDWI (experimental)'

    def group(self):
        return 'Satelite Cadente'

    def groupId(self):
        return 'imagens_satellite'

    def shortHelpString(self):
        return ('Converte DN WPM L4 para reflectancia TOA usando coeficientes absolutos e elevacao solar dos XML. '
            'Os XML sao lidos localmente ou baixados do INPE para a mesma cena. '
            'ESUN editavel na ordem azul;verde;vermelho;NIR. Padrao publicado na referencia UNESP, '
            'PDF p.43/54 (impressas 38/49); nao e uma tabela oficial atualizada em orbita. '
            'Produz quatro bandas TOA e NDVI/SAVI/NDWI de 8 m com Jenks, rotulos e QML. '
            'TOA EXPERIMENTAL: nao realiza correcao atmosferica nem mascara automatica de nuvens/sombras. '
            'Inspecione os resultados; nao comparar diretamente com reflectancia de superficie. '
            'Exclui DN zero e extremo 1023 (10 bits); nao corta reflectancias acima de 1. '
            'Referencia: https://repositorio.unesp.br/bitstreams/afe83c8d-ef8d-4f4a-9b30-79dc37f7f1e3/download')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile('BANDS_FOLDER', 'Pasta CBERS DN com CBERS_METODOLOGIA.json',
            behavior=QgsProcessingParameterFile.Behavior.Folder))
        self.addParameter(QgsProcessingParameterString('ESUN', 'ESUN azul;verde;vermelho;NIR (W/m2/um)',
            defaultValue='1958;1852;1559;1091'))
        self.addParameter(QgsProcessingParameterNumber('SAVI_L', 'L do SAVI', QgsProcessingParameterNumber.Type.Double,
            defaultValue=0.5, minValue=0, maxValue=1))
        self.addParameter(QgsProcessingParameterFolderDestination('OUTPUT', 'Pasta TOA (nova)'))
        for key in ('NDVI', 'SAVI', 'NDWI'):
            self.addOutput(QgsProcessingOutputRasterLayer(key, key + ' TOA experimental 8 m'))
        self.addOutput(QgsProcessingOutputFile('REPORT', 'Relatorio de calibracao TOA'))

    def processAlgorithm(self, parameters, context, feedback):
        from .satellite_mosaic_qgis import calibrate_cbers_wpm
        try:
            esun = tuple(float(value.strip().replace(',', '.')) for value in
                self.parameterAsString(parameters, 'ESUN', context).split(';'))
        except ValueError as exc:
            raise RuntimeError('ESUN: informe quatro numeros separados por ponto e virgula.') from exc
        feedback.pushWarning('TOA experimental: sem correcao atmosferica e sem mascara de nuvens/sombras.')
        folder = self.parameterAsString(parameters, 'OUTPUT', context)
        result = calibrate_cbers_wpm(self.parameterAsFile(parameters, 'BANDS_FOLDER', context), folder,
            esun, self.parameterAsDouble(parameters, 'SAVI_L', context), feedback)
        return {'OUTPUT': folder, 'REPORT': str(Path(folder) / 'CALIBRACAO_TOA.json'),
                **{key.upper(): result[key] for key in ('ndvi', 'savi', 'ndwi')}}

    def createInstance(self):
        return CbersToaAlgorithm()


class CbersMosaicAlgorithm(QgsProcessingAlgorithm):
    def name(self):
        return 'cbers4a_mosaico_toa'

    def displayName(self):
        return 'CBERS-4A WPM - mosaico multicena TOA (experimental)'

    def group(self):
        return 'Satelite Cadente'

    def groupId(self):
        return 'imagens_satellite'

    def shortHelpString(self):
        return ('Informe uma pasta TOA por linha, em ordem de prioridade. Cada pasta deve conter '
            'CALIBRACAO_TOA.json, produzido pelo algoritmo de calibracao CBERS. '
            'Baixe cenas complementares por ID e calibre cada uma antes do mosaico. '
            'Todas as cenas devem estar na mesma zona UTM e usar o mesmo ESUN e L do SAVI. '
            'A primeira observacao valida nas quatro bandas e indices ocupa o pixel; nao mistura '
            'vermelho de uma cena com NIR de outra. Gera bandas, indices classificados e mapa '
            'SCENE_INDEX (1 = primeira pasta), alem de relatorio e recorte opcional por poligono. '
            'Pode reunir datas diferentes. NAO detecta nuvens/sombras. Lacunas sem observacao '
            'continuam NoData; nao promete cobertura total nem reflectancia de superficie.')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString('FOLDERS', 'Pastas TOA (uma por linha, em ordem de prioridade)', multiLine=True))
        self.addParameter(QgsProcessingParameterVectorLayer('MASK','Delimitacao para recorte',
            types=[QgsProcessing.TypeVectorPolygon],optional=True))
        self.addParameter(QgsProcessingParameterFolderDestination('OUTPUT','Pasta de saida (nova)'))
        for key in ('NDVI','SAVI','NDWI','SCENE_INDEX'):
            self.addOutput(QgsProcessingOutputRasterLayer(key,key+' mosaico CBERS TOA'))
        self.addOutput(QgsProcessingOutputFile('REPORT','Relatorio multicena'))

    def processAlgorithm(self, parameters, context, feedback):
        from .satellite_mosaic_qgis import mosaic_cbers_toa
        folders=[v.strip() for v in self.parameterAsString(parameters,'FOLDERS',context).splitlines() if v.strip()]
        layer=self.parameterAsVectorLayer(parameters,'MASK',context)
        mask=QgsProcessingUtils.convertToCompatibleFormat(layer,False,'limite_cbers',['shp'],'shp',context,feedback) if layer else None
        folder=self.parameterAsString(parameters,'OUTPUT',context)
        result=mosaic_cbers_toa(folders,folder,mask,feedback)
        return {'OUTPUT':folder,'REPORT':str(Path(folder)/'MOSAICO_CBers_TOA.json'),
            **{k.upper():result[k] for k in ('ndvi','savi','ndwi','SCENE_INDEX')}}

    def createInstance(self):
        return CbersMosaicAlgorithm()


class EcostressSearchAlgorithm(QgsProcessingAlgorithm):
    def name(self):
        return "ecostress_buscar"

    def displayName(self):
        return "NASA ECOSTRESS - consultar e baixar L2T LSTE"

    def group(self):
        return "Satelite Cadente"

    def groupId(self):
        return "imagens_satellite"

    def shortHelpString(self):
        return ("Consulta publica NASA CMR, produto ECO_L2T_LSTE.003, tiles 70 m. "
            "Primeiro execute com ID vazio e confira o catalogo JSON. Para baixar, execute novamente com "
            "o ID exato de um granulo e autenticacao Basica QGIS: Usuario=Earthdata, Senha=token EDL. "
            "Crie conta gratuita em https://urs.earthdata.nasa.gov/users/new e gere token no seu perfil. "
            "Token fica criptografado no QGIS, nunca no projeto/relatorio. Baixa LST, QC e cloud da mesma "
            "aquisicao; depois use Preparar LST. Cada tile pode cobrir somente parte do limite; nao mistura "
            "temperaturas de dias/horarios diferentes. Datas em UTC. Referencia: " + METHOD_REFERENCES["ecostress"])

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterExtent("EXTENT", "Extensao da consulta"))
        self.addParameter(QgsProcessingParameterString("START", "Data inicial UTC (AAAA-MM-DD)", "2025-01-01"))
        self.addParameter(QgsProcessingParameterString("END", "Data final UTC (AAAA-MM-DD)", date.today().isoformat()))
        self.addParameter(QgsProcessingParameterString("GRANULE", "ID exato do granulo (vazio = apenas consultar)", optional=True))
        auth = QgsProcessingParameterAuthConfig("EARTHDATA_AUTH", "Earthdata (token no campo Senha)", None, True)
        auth.setHelp("Configuracao Basica do QGIS. Usuario=Earthdata; Senha=token EDL, nao sua senha da conta. Nunca envie o token no chat.")
        self.addParameter(auth)
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT", "Pasta de catalogo e download"))
        self.addOutput(QgsProcessingOutputFile("CATALOG", "Catalogo ECOSTRESS (JSON)"))

    def processAlgorithm(self, parameters, context, feedback):
        extent = self.parameterAsExtent(parameters, "EXTENT", context, QgsCoordinateReferenceSystem("EPSG:4326"))
        granule = self.parameterAsString(parameters, "GRANULE", context).strip()
        token = None
        if granule:
            auth_id = self.parameterAsString(parameters, "EARTHDATA_AUTH", context)
            config = QgsAuthMethodConfig()
            if not auth_id or not QgsApplication.authManager().loadAuthenticationConfig(auth_id, config, True):
                raise RuntimeError("Configure autenticacao Earthdata no QGIS; consulte o catalogo sem ID para dispensar login.")
            token = config.config("password")
        folder = self.parameterAsString(parameters, "OUTPUT", context)
        catalog, downloaded = search_ecostress((extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()),
            self.parameterAsString(parameters, "START", context), self.parameterAsString(parameters, "END", context),
            folder, granule, token, feedback)
        if downloaded:
            feedback.pushInfo("Granulo pronto: " + downloaded + "; use Gerar produtos > NASA ECOSTRESS: preparar LST.")
        return {"OUTPUT": folder, "CATALOG": catalog}

    def createInstance(self):
        return EcostressSearchAlgorithm()


class EcostressLstAlgorithm(QgsProcessingAlgorithm):
    def name(self):
        return "ecostress_lst"

    def displayName(self):
        return "NASA ECOSTRESS - preparar e classificar LST (70 m)"

    def group(self):
        return "Satelite Cadente"

    def groupId(self):
        return "imagens_satellite"

    def shortHelpString(self):
        return ("Use GeoTIFFs originais L2T LSTE V002/V003 Float32 em Kelvin, na grade UTM 70 m, "
            "da mesma aquisicao: LST, QC e cloud. Converte Kelvin para Celsius; conserva cloud=0 e QC "
            "bits 0-3=0000. Rejeita produto inteiro/HDF, nomes alterados, grids desalinhados e mascaras "
            "interpoladas. Nao filtra todos os indicadores de erro/emissividade. Recorte opcional por "
            "poligono; nao completa lacunas nem mistura horarios. LST nao e temperatura do ar. "
            "Saidas: Float32 -9999, QML com rotulos e JSON com areas por classe/metodologia. "
            "Comparacoes exigem horario, sazonalidade e limites equivalentes. " + METHOD_REFERENCES["ecostress"])

    def initAlgorithm(self, config=None):
        for key, title in (("LST", "LST original (Kelvin)"), ("QC", "QC original"), ("CLOUD", "cloud original")):
            self.addParameter(QgsProcessingParameterFile(key, title, extension="tif"))
        self.addParameter(QgsProcessingParameterVectorLayer("MASK", "Limite poligonal", [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterEnum("CLASSIFICATION", "Classificacao visual", list(CLASSIFICATION_METHODS.values()), defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber("CLASS_COUNT", "Numero de classes", QgsProcessingParameterNumber.Type.Integer, 5, minValue=2, maxValue=20))
        self.addParameter(QgsProcessingParameterString("CLASS_LIMITS", "Limites manuais (separados por ;)", optional=True))
        self.addParameter(QgsProcessingParameterBoolean("THERMAL_1C", "Classes de 1 Celsius", False))
        self.addParameter(QgsProcessingParameterRasterDestination("OUTPUT", "LST classificada"))
        self.addOutput(QgsProcessingOutputFile("REPORT", "Relatorio de LST (JSON)"))

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsVectorLayer(parameters, "MASK", context)
        mask = QgsProcessingUtils.convertToCompatibleFormat(layer, False, "limite_ecostress", ["shp"], "shp", context, feedback) if layer else None
        output = self.parameterAsOutputLayer(parameters, "OUTPUT", context)
        index = self.parameterAsEnum(parameters, "CLASSIFICATION", context)
        if not 0 <= index < len(CLASSIFICATION_METHODS):
            raise RuntimeError("Metodo de classificacao invalido.")
        result, report = generate_ecostress_lst(self.parameterAsFile(parameters, "LST", context),
            self.parameterAsFile(parameters, "QC", context), self.parameterAsFile(parameters, "CLOUD", context), output,
            feedback, mask, tuple(CLASSIFICATION_METHODS)[index], self.parameterAsInt(parameters, "CLASS_COUNT", context),
            self.parameterAsString(parameters, "CLASS_LIMITS", context), self.parameterAsBool(parameters, "THERMAL_1C", context))
        _register_output(context, output, result, ClassifiedPostProcessor())
        return {"OUTPUT": result, "REPORT": report}

    def createInstance(self):
        return EcostressLstAlgorithm()


class SateliteCadenteProvider(QgsProcessingProvider):
    def id(self):
        return "imagens_satellite"

    def name(self):
        return "Satelite Cadente"

    def longName(self):
        return self.name()

    def loadAlgorithms(self):
        self.addAlgorithm(SateliteCadenteAlgorithm())
        self.addAlgorithm(SateliteCadenteAnalyticProductAlgorithm())
        self.addAlgorithm(DroneOrthomosaicAlgorithm())
        self.addAlgorithm(CbersWpmAlgorithm())
        self.addAlgorithm(CbersToaAlgorithm())
        self.addAlgorithm(CbersMosaicAlgorithm())
        self.addAlgorithm(CbersCoverageAlgorithm())
        self.addAlgorithm(EcostressSearchAlgorithm())
        self.addAlgorithm(EcostressLstAlgorithm())


if __name__ == "__main__":
    _self_check()

