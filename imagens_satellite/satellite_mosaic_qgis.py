r"""Baixa, mosaica e recorta fontes XYZ, Sentinel-2 e Landsat pela extensão escolhida.

No Console Python do QGIS, execute:
exec(open(r"D:/GIS/Script/satellite_mosaic_qgis.py", encoding="utf-8").read())
"""

import json
import errno
from shutil import copyfile
import threading
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from calendar import monthrange
from math import ceil, floor
from os import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    Qgis,
    QgsClassificationEqualInterval,
    QgsClassificationJenks,
    QgsClassificationQuantile,
    QgsClassificationStandardDeviation,
    QgsColorRampShader,
    QgsColorRampLegendNodeSettings,
    QgsContrastEnhancement,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMultiBandColorRenderer,
    QgsProject,
    QgsPointXY,
    QgsRasterBandStats,
    QgsRasterLayer,
    QgsRasterShader,
    QgsRasterTransparency,
    QgsRectangle,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.utils import iface

CLOUDLESS_URL_TEMPLATE = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-{year}_3857/default/g/{z}/{y}/{x}.jpg"
ESRI_URL = "https://tiledbasemaps.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
GOOGLE_URL_TEMPLATE = "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
GOOGLE_COPYRIGHT = "Google Satellite; atribuição e termos do serviço Google"
BING_URL_TEMPLATE = "https://ecn.t3.tiles.virtualearth.net/tiles/a${quadkey}.jpeg?g=1&amp;mkt=en-us"
BING_COPYRIGHT = "Microsoft Bing Virtual Earth; atribuição e termos do serviço Microsoft"
CLOUDLESS_COPYRIGHT = "EOX Cloudless; contém dados Copernicus Sentinel modificados"
ESRI_COPYRIGHT = "Esri, Vantor, Earthstar Geographics e GIS User Community"
EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1/search"
PLANETARY_COMPUTER_SEARCH_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
PLANETARY_COMPUTER_SAS_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
CLOUDLESS_YEARS = (2025, 2024, 2023, 2022, 2021, 2020)
L2A_YEARS = tuple(range(date.today().year, 2018, -1))
LANDSAT45_YEARS = tuple(range(2013, 1981, -1))
LANDSAT7_YEARS = tuple(range(2025, 1998, -1))
LANDSAT7_SLC_ON_YEARS = (2002, 2001, 2000, 1999)
LANDSAT89_YEARS = tuple(range(date.today().year, 2012, -1))
# Sentinel-2 Cloudless tem resolução nativa de aproximadamente 10 m; acima de Z14
# há apenas ampliação dos mesmos pixels, sem ganho real de detalhe.
CLOUDLESS_ZOOMS = (14, 13, 12)
# Esri pode conter aerofotos urbanas muito detalhadas. Fora dessas áreas, inclusive
# no semiárido baiano, Z18/Z19 costuma ser reamostragem sem detalhe novo sobre Z17/Z18.
ESRI_ZOOMS = (19, 18, 17, 16, 15, 14)
GOOGLE_ZOOMS = tuple(range(20, 11, -1))
BING_ZOOMS = tuple(range(19, 11, -1))
# Sentinel-2 L2A já é baixado na resolução nativa de cada banda (10/20/60 m),
# portanto não existe seleção de "zoom" nesse modo.
L2A_BANDS = (
    ("coastal", "B01", 60, "bilinear"),
    ("blue", "B02", 10, "bilinear"),
    ("green", "B03", 10, "bilinear"),
    ("red", "B04", 10, "bilinear"),
    ("rededge1", "B05", 20, "bilinear"),
    ("rededge2", "B06", 20, "bilinear"),
    ("rededge3", "B07", 20, "bilinear"),
    ("nir", "B08", 10, "bilinear"),
    ("nir08", "B8A", 20, "bilinear"),
    ("nir09", "B09", 60, "bilinear"),
    ("swir16", "B11", 20, "bilinear"),
    ("swir22", "B12", 20, "bilinear"),
    ("scl", "SCL", 20, "near"),
    ("cloud", "CLD", 20, "bilinear"),
    ("snow", "SNW", 20, "bilinear"),
    ("aot", "AOT", 20, "bilinear"),
    ("wvp", "WVP", 20, "bilinear"),
)
LANDSAT45_BANDS = (
    ("blue", "SR_B1", 30, "bilinear"),
    ("green", "SR_B2", 30, "bilinear"),
    ("red", "SR_B3", 30, "bilinear"),
    ("nir08", "SR_B4", 30, "bilinear"),
    ("swir16", "SR_B5", 30, "bilinear"),
    ("lwir", "ST_B6", 30, "bilinear"),
    ("swir22", "SR_B7", 30, "bilinear"),
    ("qa_pixel", "QA_PIXEL", 30, "near"),
)
LANDSAT7_BANDS = LANDSAT45_BANDS
LANDSAT89_BANDS = (
    ("coastal", "SR_B1", 30, "bilinear"),
    ("blue", "SR_B2", 30, "bilinear"),
    ("green", "SR_B3", 30, "bilinear"),
    ("red", "SR_B4", 30, "bilinear"),
    ("nir08", "SR_B5", 30, "bilinear"),
    ("swir16", "SR_B6", 30, "bilinear"),
    ("swir22", "SR_B7", 30, "bilinear"),
    ("lwir11", "ST_B10", 30, "bilinear"),
    ("qa_pixel", "QA_PIXEL", 30, "near"),
    ("qa_aerosol", "SR_QA_AEROSOL", 30, "near"),
)
HLS_L30_BANDS = (
    ("coastal", "B01", 30, "near"), ("blue", "B02", 30, "near"),
    ("green", "B03", 30, "near"), ("red", "B04", 30, "near"),
    ("nir08", "B05", 30, "near"), ("swir16", "B06", 30, "near"),
    ("swir22", "B07", 30, "near"), ("fmask", "Fmask", 30, "near"),
)
HLS_S30_BANDS = (
    ("coastal", "B01", 30, "near"), ("blue", "B02", 30, "near"),
    ("green", "B03", 30, "near"), ("red", "B04", 30, "near"),
    ("rededge1", "B05", 30, "near"), ("rededge2", "B06", 30, "near"),
    ("rededge3", "B07", 30, "near"), ("nir", "B08", 30, "near"),
    ("nir08", "B8A", 30, "near"), ("nir09", "B09", 30, "near"),
    ("swir16", "B11", 30, "near"), ("swir22", "B12", 30, "near"),
    ("fmask", "Fmask", 30, "near"),
)
MULTIBAND_SOURCES = {
    "l2a": {
        "title": "Sentinel-2 L2A",
        "folder": "Sentinel2_L2A_Multibanda",
        "collection": "sentinel-2-c1-l2a",
        "catalog": EARTH_SEARCH_URL,
        "platforms": (),
        "years": L2A_YEARS,
        "bands": L2A_BANDS,
        "preview": "B04",
        "source": "Copernicus Sentinel-2 Collection 1 L2A via Element 84 Earth Search / AWS Open Data",
        "level": "L2A",
        "rights": "Contém dados Copernicus Sentinel modificados",
        "license": "Copernicus Data Space Ecosystem Legal Notice",
        "calibration": "Reflectancia = DN x escala + offset do asset STAC; a correcao depende do processamento da cena.",
    },
    "landsat45": {
        "title": "Landsat 4-5 TM",
        "folder": "Landsat45_TM_Multibanda",
        "collection": "landsat-c2-l2",
        "catalog": PLANETARY_COMPUTER_SEARCH_URL,
        "platforms": ("landsat-4", "landsat-5"),
        "years": LANDSAT45_YEARS,
        "bands": LANDSAT45_BANDS,
        "preview": "SR_B3",
        "source": "USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer",
        "level": "Collection 2 Level-2",
        "rights": "Dados Landsat/USGS em domínio público; atribuição solicitada",
        "license": "USGS Public Domain",
        "calibration": "Reflectância = DN × 0,0000275 - 0,2; temperatura = DN × 0,00341802 + 149 K.",
    },
    "landsat7": {
        "title": "Landsat 7 ETM+",
        "folder": "Landsat7_ETM_Multibanda",
        "collection": "landsat-c2-l2",
        "catalog": PLANETARY_COMPUTER_SEARCH_URL,
        "platforms": ("landsat-7",),
        "years": LANDSAT7_YEARS,
        "default_years": LANDSAT7_SLC_ON_YEARS,
        "bands": LANDSAT7_BANDS,
        "preview": "SR_B3",
        "source": "USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer",
        "level": "Collection 2 Level-2",
        "rights": "Dados Landsat/USGS em domínio público; atribuição solicitada",
        "license": "USGS Public Domain",
        "calibration": "Reflectância = DN × 0,0000275 - 0,2; temperatura = DN × 0,00341802 + 149 K.",
    },
    "landsat89": {
        "title": "Landsat 8-9 OLI/TIRS",
        "folder": "Landsat89_OLI_TIRS_Multibanda",
        "collection": "landsat-c2-l2",
        "catalog": PLANETARY_COMPUTER_SEARCH_URL,
        "platforms": ("landsat-8", "landsat-9"),
        "years": LANDSAT89_YEARS,
        "bands": LANDSAT89_BANDS,
        "preview": "SR_B4",
        "source": "USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer",
        "level": "Collection 2 Level-2",
        "rights": "Dados Landsat/USGS em domínio público; atribuição solicitada",
        "license": "USGS Public Domain",
        "calibration": "Reflectância = DN × 0,0000275 - 0,2; temperatura = DN × 0,00341802 + 149 K.",
    },
}
for _source, _sensor, _bands in (("hls_l30", "Landsat", HLS_L30_BANDS),
                                ("hls_s30", "Sentinel-2", HLS_S30_BANDS)):
    MULTIBAND_SOURCES[_source] = {
        "title": f"NASA HLS v2 {_sensor} (30 m)", "folder": _source.upper(),
        "collection": "hls2-" + _source.removeprefix("hls_"),
        "catalog": PLANETARY_COMPUTER_SEARCH_URL, "platforms": (),
        "years": tuple(range(date.today().year, 2019, -1)), "bands": _bands,
        "preview": "B04", "quality": "fmask",
        "source": "NASA HLS v2 / LP DAAC via Microsoft Planetary Computer (espelho desde 2020)",
        "level": "HLS v2 surface reflectance harmonized", "rights": "NASA/USGS/Copernicus; atribuir HLS e fontes originais",
        "license": "NASA Earthdata open data; termos Copernicus para Sentinel-2",
        "calibration": "Reflectancia = DN x 0,0001; NoData -9999; QA Fmask NoData 255. NIR S30 = B8A. Sem produto LST.",
    }
MULTIBAND_RGB_PREVIEW = {source: "RGB_Visual.vrt" for source in MULTIBAND_SOURCES}
SPECTRAL_INDICES = {
    "l2a": ("ndvi", "ndwi", "ndbi", "savi", "evi2"),
    "landsat45": ("ndvi", "ndwi", "ndbi", "savi", "evi2", "lst"),
    "landsat7": ("ndvi", "ndwi", "ndbi", "savi", "evi2", "lst"),
    "landsat89": ("ndvi", "ndwi", "ndbi", "savi", "evi2", "lst"),
    "hls_l30": ("ndvi", "ndwi", "ndbi", "savi", "evi2"),
    "hls_s30": ("ndvi", "ndwi", "ndbi", "savi", "evi2"),
    "drone": ("vari", "ndvi", "ndwi", "savi"),
}
ANALYTIC_PRODUCTS = ("rgb", "ndvi", "ndwi", "ndbi", "savi", "composition", "lst", "lst_fused", "evi2")
DRONE_PRODUCTS = ("vari", "ndvi", "savi", "ndwi")
BAND_PROFILES = ("complete", "vegetation", "indices")


def _download_band_specs(source, profile):
    if profile not in BAND_PROFILES:
        raise RuntimeError("Perfil de bandas invalido.")
    bands = MULTIBAND_SOURCES[source]["bands"]
    if profile == "complete":
        return bands
    keys = {"red", "green", "blue", "nir" if source == "l2a" else "nir08",
            _quality_key(source)}
    if profile == "indices":
        keys.add("swir16")
    return tuple(band for band in bands if band[0] in keys)


CLASSIFICATION_METHODS = {
    "equal": "Intervalos iguais",
    "quantile": "Quantis (mesma quantidade aproximada de pixels)",
    "jenks": "Quebras naturais (Jenks)",
    "stddev": "Desvio-padrao (classes relativas a media)",
    "manual": "Limites manuais (limiares definidos pelo usuario)",
}
RGB_COMPOSITIONS = {
    "natural": ("red", "green", "blue"),
    "vegetation": ("nir", "red", "green"),
    "urban": ("swir16", "nir", "red"),
}
METHOD_REFERENCES = {
    "rgb": "Composição RGB para inspeção visual; não é índice espectral nem medida biofísica.",
    "composition": "Composição colorida por combinação de bandas; interpretação visual dependente do sensor e da data.",
    "ndvi": "Rouse, J. W. et al. (1974). Monitoring vegetation systems in the Great Plains with ERTS.",
    "ndwi": "McFeeters, S. K. (1996). The use of the Normalized Difference Water Index (NDWI) in the delineation of open water features.",
    "ndbi": "Zha, Y.; Gao, J.; Ni, S. (2003). Use of normalized difference built-up index in automatically mapping urban areas from TM imagery.",
    "savi": "Huete, A. R. (1988). A soil-adjusted vegetation index (SAVI); L configuravel, padrao 0,5; valor efetivo em SAVI_L.",
    "evi2": "Jiang, Z. et al. (2008). Development of a two-band enhanced vegetation index without a blue band. Remote Sensing of Environment 112, 3833-3845. DOI: 10.1016/j.rse.2008.06.006.",
    "vari": "Gitelson, A. A. et al. (2002). Novel algorithms for remote estimation of vegetation fraction. Remote Sensing of Environment, 80, 76-87.",
    "lst": "USGS Landsat Collection 2 Level-2 Surface Temperature; escala e offset oficiais do produto ST.",
    "lst_fused": "Agam et al. (2007), Remote Sensing of Environment 107, 545-558; TsHARP adaptado por cena. DOI: 10.1016/j.rse.2006.10.006.",
    "quality": "ESA/Sen2Cor Scene Classification Layer (SCL) e USGS Landsat QA_PIXEL para controle de qualidade por pixel.",
    "hls": "NASA HLS v2: reflectancia harmonizada 30 m, NIR S30 B8A, Fmask. https://hls.gsfc.nasa.gov/data-products/ ; https://hls.gsfc.nasa.gov/algorithms/ .",
    "ecostress": "NASA/JPL ECO_L2T_LSTE.003, DOI 10.5067/ECOSTRESS/ECO_L2T_LSTE.003; ECOL2_User_Guide_V3.pdf, p. 9-10 e 18-20 (QA).",
    "landsat7_gapfill": (
        "Landsat 7 SLC-off: composição multitemporal com QA_PIXEL por cena e prioridade a pixels válidos; "
        "a falha do SLC e o preenchimento por múltiplas cenas são documentados pelo USGS em "
        "Landsat 7 SLC-off Gap-Filled Products Phase Two Methodology e por Franco (2017), p. 5."
    ),
}
METHOD_REFERENCES_TEXT = "\n".join(f"- {reference}" for reference in METHOD_REFERENCES.values())
CHUNK_PIXELS = 2048
RETRIES = 3
REFERENCES_NAME = "REFERENCIAS_E_ATRIBUICAO.txt"
RGB_METHODOLOGY = (
    "Metodologia RGB: a extensão informada é transformada para EPSG:3857; "
    "a resolução nominal é calculada por 156543,03392804097 / 2^zoom; "
    "os fragmentos XYZ/TMS são baixados em paralelo, mosaico via GDAL/VRT, "
    "reamostrados por Lanczos, recortados pelo bounding box e gravados como "
    "GeoTIFF RGB+alpha. Tiles cinzentos de erro/sem cobertura viram NoData "
    "transparente; Esri tenta o próximo zoom inferior quando todo o Z19 vem "
    "como 'Map data not yet available'."
)
MULTIBAND_METHODOLOGY = (
    "Consulta STAC paginada por ano, missao e nuvens; qualidade local por SCL/QA_PIXEL/Fmask. "
    "A saida analitica usa a grade UTM da cena de referencia, alinhada ao pixel e "
    "reamostrada somente por vizinho mais proximo, sem interpolar DN. "
    "Um mapa de observacoes seleciona a mesma cena por pixel para as bandas opticas; "
    "bandas opcionais ausentes nessa observacao permanecem NoData. Sentinel-2 conserva SCL 4/5/6; "
    "Landsat remove preenchimento, nuvem, cirrus, sombra e neve. Escala e offset sao "
    "lidos dos assets e gravados nas bandas. A cobertura final e medida, sem inventar "
    "dados nem prometer eliminar lacunas SLC-off. Indices usam reflectancia calibrada; "
    "LST usa o produto USGS ST em Kelvin, convertido para Celsius. Sentinel usa Collection 1, "
    "sem fallback para COGs legados com offset ambiguo. Indices opticos excluem reflectancias "
    "negativas ou denominadores nulos; os DN originais permanecem intactos. HLS usa DN x 0,0001, "
    "Fmask (nuvem, adjacencia, sombra, neve e aerosol alto excluidos), NIR S30 B8A e grade 30 m. "
    "HLS nao fornece LST; mosaico multidata nao e uma serie temporal pronta."
)
WEB_MERCATOR_RESOLUTION_0 = 156543.03392804097
# Limite (em metros) do mundo inteiro projetado em EPSG:3857 — usado para
# descrever a pirâmide de tiles completa ao GDAL (nível 0 = mundo inteiro).
WEB_MERCATOR_WORLD_EXTENT = 20037508.342789244
# Gatilho de aviso de segurança; nunca bloqueia um zoom automaticamente.
MAX_PIXELS = 250_000_000
# Cada fragmento agora é buscado em sua própria thread via GDAL (a biblioteca
# libera o GIL do Python durante a espera de rede), o que multiplica a
# velocidade real de download. 6 é um equilíbrio entre desempenho e o risco
# de respostas HTTP 429 em servidores públicos gratuitos; a nova tentativa
# com espera exponencial (RETRIES) absorve falhas ocasionais.
CHUNK_WORKERS = 6
ANALYTIC_DOWNLOAD_WORKERS = 3
NETWORK_OPTIONS = {
    "GDAL_HTTP_VERSION": "2TLS",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_HTTP_TIMEOUT": "60",
    "GDAL_HTTP_CONNECTTIMEOUT": "15",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
}
MANIFEST_NAME = "BANDAS_MANIFEST.json"
VERSION = "3.0.0"
DRONE_SOURCE = {
    "title": "Ortomosaico de drone",
    "source": "Ortomosaico local informado pelo usuario",
    "rights": "Direitos e licenca dependem do levantamento informado",
    "license": "Nao informada pelo complemento",
}
LST_NODATA_NOTE = (
    "LST pode permanecer sem dados por ausencia de emissividade ASTER GED no produto USGS ST, "
    "mesmo com bandas opticas validas. Essas lacunas podem persistir entre anos; "
    "o complemento nao interpola temperaturas nem troca a data apenas da banda termica."
)


def _check_cancel(feedback):
    if feedback is not None and feedback.isCanceled():
        raise RuntimeError("Processamento cancelado pelo usuario.")


def _gdal_progress(feedback):
    return lambda progress, message, data: int(feedback is None or not feedback.isCanceled())


def _build_overviews(dataset, resampling="AVERAGE", feedback=None):
    levels = [level for level in (2, 4, 8, 16, 32, 64) if min(dataset.RasterXSize, dataset.RasterYSize) >= level]
    if levels:
        with gdal.config_option("COMPRESS_OVERVIEW", "DEFLATE"):
            dataset.BuildOverviews(resampling, levels, callback=_gdal_progress(feedback))
    _check_cancel(feedback)

def _grid(bounds, columns, rows):
    xmin, ymin, xmax, ymax = bounds
    width, height = (xmax - xmin) / columns, (ymax - ymin) / rows
    for row in range(rows):
        for column in range(columns):
            yield (
                xmin + column * width,
                ymin + row * height,
                xmin + (column + 1) * width,
                ymin + (row + 1) * height,
            )


def _zoom_and_size(bounds, zoom):
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    resolution = WEB_MERCATOR_RESOLUTION_0 / (2**zoom)
    columns, rows = ceil(width / resolution), ceil(height / resolution)
    return zoom, resolution, max(1, columns), max(1, rows)


def _available_zooms(_bounds, zooms):
    return tuple(zooms)


def _format_eta(seconds):
    """Formata o ETA sem bibliotecas externas."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} s"
    return f"{seconds} s"


def _download_plan(bounds, zoom):
    """Calcula a grade uma vez; serve também para estimativa e futura prévia."""
    zoom, resolution, total_columns, total_rows = _zoom_and_size(bounds, zoom)
    grid_columns = ceil(total_columns / CHUNK_PIXELS)
    grid_rows = ceil(total_rows / CHUNK_PIXELS)
    return {
        "zoom": zoom,
        "resolution": resolution,
        "total_columns": total_columns,
        "total_rows": total_rows,
        "total_pixels": total_columns * total_rows,
        "parts": list(_grid(bounds, grid_columns, grid_rows)),
        "grid_columns": grid_columns,
        "grid_rows": grid_rows,
    }


def _asset_url(href):
    if not href.startswith("s3://"):
        return href
    bucket, key = href[5:].split("/", 1)
    return f"https://{bucket}.s3.amazonaws.com/{key}"


def _authorized_asset_url(href, token=None):
    url = _asset_url(href)
    if token:
        url = f"{url}{'&' if '?' in url else '?'}{token}"
    return url




def _scene_tile(item):
    properties = item["properties"]
    tile = properties.get("grid:code")
    if not tile and item["id"].startswith("HLS."):
        tile = item["id"].split(".")[2]
    if not tile and properties.get("landsat:wrs_path") and properties.get("landsat:wrs_row"):
        tile = f"WRS2-{properties['landsat:wrs_path']}{properties['landsat:wrs_row']}"
    return tile or item["id"].split("_")[1]




def _greedy_scene_coverage(masks, max_scenes):
    covered = np.zeros_like(masks[0][1], dtype=bool)
    selected = []
    while masks and len(selected) < max_scenes:
        item, mask = max(
            masks,
            key=lambda pair: (
                np.count_nonzero(pair[1] & ~covered),
                -float(pair[0]["properties"].get("eo:cloud_cover", 100)),
                pair[0]["properties"]["datetime"],
            ),
        )
        gain = np.count_nonzero(mask & ~covered)
        if not gain:
            break
        selected.append(item)
        covered |= mask
        masks = [pair for pair in masks if pair[0]["id"] != item["id"]]
        if covered.mean() >= 0.98:
            break
    return selected, float(covered.mean())


def _select_scenes_by_local_quality(features, source, bounds_wgs84, token=None, feedback=None):
    quality_key = _quality_key(source)
    by_tile = {}
    for item in features:
        by_tile.setdefault(_scene_tile(item), []).append(item)
    by_tile = {
        tile: sorted(
            items,
            key=lambda value: (
                float(value["properties"].get("eo:cloud_cover", 100)),
                value["properties"]["datetime"],
            ),
        )
        for tile, items in by_tile.items()
    }

    masks = []
    def read_quality_mask(item):
        nonlocal token
        asset = item.get("assets", {}).get(quality_key)
        if not asset:
            return
        def renew_source():
            nonlocal token
            token = _planetary_computer_token(MULTIBAND_SOURCES[source]["collection"])
            return _asset_path(asset["href"], token)

        dataset = _warp_remote_with_retry(
                None, _asset_path(asset["href"], token),
                gdal.WarpOptions(
                    format="MEM",
                    dstSRS="EPSG:4326",
                    outputBounds=bounds_wgs84,
                    outputBoundsSRS="EPSG:4326",
                    width=512,
                    height=512,
                    resampleAlg="near",
                    srcNodata=_asset_nodata(source, quality_key),
                    dstNodata=_asset_nodata(source, quality_key),
                    errorThreshold=0.0,
                    callback=_gdal_progress(feedback),
                ),
                f"{quality_key} da cena {item['id']}", feedback,
                renew_source if token else None,
        )
        if dataset is None:
            return
        masks.append((item, ~_quality_invalid(source, dataset.ReadAsArray())))
        dataset.Close()

    # ponytail: stop SCL/QA reads once coverage is strong; raise 98% if stricter completeness is needed.
    max_rounds = max(map(len, by_tile.values()))
    for rank in range(max_rounds):
        for items in by_tile.values():
            if rank < len(items):
                _check_cancel(feedback)
                read_quality_mask(items[rank])
        selected, coverage = _greedy_scene_coverage(masks, min(len(masks), 12 * len(by_tile))) if masks else ([], 0.0)
        if rank >= 1 and coverage >= 0.98:
            break

    if not masks:
        raise RuntimeError("Nao foi possivel ler SCL/QA_PIXEL para avaliar cenas; verifique o acesso ao catalogo.")

    if feedback is not None:
        feedback.pushInfo(
            f"Qualidade local no recorte: {coverage:.1%} de pixels válidos com {len(selected)} cena(s)."
        )
    ids = {item["id"] for item in selected}
    fallback = []
    fallback_tiles = set()
    for item, mask in masks:
        tile = _scene_tile(item)
        if item["id"] not in ids and tile not in fallback_tiles and mask.any():
            fallback.append(item)
            fallback_tiles.add(tile)
    scenes = selected + fallback
    return scenes, len({item["properties"]["datetime"][:10] for item in scenes}) == 1, coverage


def _candidate_years(year, config):
    return (year,) if year is not None else config.get("default_years", config["years"])


def _stac_features(request, feedback):
    features = []
    origin = urlparse(request.full_url)
    for _ in range(10):
        _check_cancel(feedback)
        with urlopen(request, timeout=60) as response:
            page = json.load(response)
        features.extend(page.get("features", []))
        link = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
        if link is None:
            return features
        method = link.get("method", "GET")
        body = json.loads(request.data or b"{}") if link.get("merge") else {}
        body.update(link.get("body", {}))
        data = json.dumps(body).encode("utf-8") if method == "POST" else None
        href = urljoin(request.full_url, link["href"])
        target = urlparse(href)
        if target.scheme != "https" or target.netloc != origin.netloc:
            raise RuntimeError("Paginacao STAC apontou para um dominio externo; consulta interrompida.")
        request = Request(href, data=data, headers={"Content-Type": "application/json"}, method=method)
    raise RuntimeError("Catalogo excede 1000 cenas; reduza a area ou restrinja os filtros.")


def _search_multiband(bounds_wgs84, year, max_cloud, config, source, token=None, feedback=None,
                      month_start=1, month_end=12):
    if any(not isinstance(value, int) or isinstance(value, bool) for value in (month_start, month_end)) or not 1 <= month_start <= month_end <= 12:
        raise RuntimeError("Meses devem ser inteiros de 1 a 12, com inicio <= fim; divida periodos que cruzam o ano.")
    query = {"eo:cloud_cover": {"lte": max_cloud}}
    if config["platforms"]:
        query["platform"] = {"in": list(config["platforms"])}
    years = _candidate_years(year, config)
    best_result = None
    for position, candidate in enumerate(years):
        if feedback is not None and feedback.isCanceled():
            return None
        payload = json.dumps(
            {
                "collections": [config["collection"]],
                "bbox": list(bounds_wgs84),
                "datetime": (f"{candidate}-{month_start:02d}-01T00:00:00Z/"
                             f"{candidate}-{month_end:02d}-{monthrange(candidate, month_end)[1]:02d}T23:59:59Z"),
                "query": query,
                "limit": 100,
            }
        ).encode("utf-8")
        request = Request(
            config["catalog"],
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "QGIS-Imagens-Satelite"},
            method="POST",
        )
        try:
            features = _stac_features(request, feedback)
            if source.startswith("hls_"):
                for item in features:
                    item["assets"] = {key: item["assets"][name] for key, name, _, _ in config["bands"]
                                      if name in item.get("assets", {})}
        except Exception as exc:
            raise RuntimeError(f"Falha ao consultar o catálogo {config['title']}: {exc}") from exc
        features = [
            item
            for item in features
            if float(item["properties"].get("eo:cloud_cover", 100)) <= max_cloud
            and f"{candidate}-{month_start:02d}" <= item["properties"]["datetime"][:7] <= f"{candidate}-{month_end:02d}"
        ]
        if features:
            scenes, same_date, coverage = _select_scenes_by_local_quality(
                features, source, bounds_wgs84, token, feedback
            )
            if scenes and (best_result is None or coverage > best_result[3]):
                best_result = scenes, same_date, candidate, coverage
            if scenes and (year is not None or coverage >= 0.98):
                if position and feedback is not None:
                    feedback.pushInfo(
                        f"Usando {config['title']} de {candidate}: ano mais recente com boa cobertura local."
                    )
                return scenes, same_date, candidate
            if feedback is not None:
                feedback.pushInfo(
                    f"{config['title']} de {candidate} cobre apenas {coverage:.1%} do recorte; buscando outro ano."
                )
        if not features and position == 0 and year is None and feedback is not None:
            feedback.pushInfo(
                f"{config['title']} não possui cenas de {candidate} no filtro; buscando anos anteriores."
            )
    if best_result is not None:
        scenes, same_date, selected_year, coverage = best_result
        if feedback is not None:
            feedback.pushWarning(
                f"Nenhum ano atingiu 98% de cobertura local; usando {selected_year} com {coverage:.1%}."
            )
        return scenes, same_date, selected_year
    if year is None:
        raise RuntimeError(
            f"Nenhuma cena {config['title']} atende ao filtro de nuvens em {years[-1]}-{years[0]}."
        )
    raise RuntimeError(f"Nenhuma cena {config['title']} atende aos filtros escolhidos em {year}.")


def _cbers_asset_url(item, key):
    href = item.get("assets", {}).get(key, {}).get("href", "")
    parsed = urlparse(href)
    if parsed.scheme != "https" or parsed.hostname != "data.inpe.br":
        raise RuntimeError(f"Asset CBERS ausente ou fora do dominio INPE: {key}.")
    return href


def search_cbers_wpm_coverage(boundary_wgs84, start_year, end_year, feedback=None):
    """Estima por geometria se ha cenas WPM para a area; sem baixar bandas."""
    from urllib.parse import urlencode
    if not 2019 <= start_year <= end_year <= date.today().year:
        raise RuntimeError('Periodo deve estar entre 2019 e o ano atual, inicio <= fim.')
    if boundary_wgs84 is None or boundary_wgs84.IsEmpty() or boundary_wgs84.GetGeometryType() not in (
            ogr.wkbPolygon, ogr.wkbMultiPolygon):
        raise RuntimeError('Informe um poligono valido em WGS84.')
    extent=boundary_wgs84.GetEnvelope()
    bounds=(extent[0],extent[2],extent[1],extent[3])
    query=urlencode({'bbox':','.join(map(str,bounds)),
        'datetime':f'{start_year}-01-01T00:00:00Z/{end_year}-12-31T23:59:59Z','limit':100})
    request=Request('https://data.inpe.br/bdc/stac/v1/collections/CB4A-WPM-L4-DN-1/items?'+query)
    items=_stac_features(request,feedback)
    candidates=[]
    for item in items:
        if not item.get('geometry'): continue
        shape=ogr.CreateGeometryFromJson(json.dumps(item['geometry']))
        if shape is None: continue
        overlap=shape.Intersection(boundary_wgs84)
        if overlap is not None and not overlap.IsEmpty() and overlap.GetArea()>0:
            candidates.append((item,overlap))
    intersecting_scenes=len(candidates)
    remaining=boundary_wgs84.Clone(); selected=[]
    while candidates:
        choices=[(shape.Intersection(remaining).GetArea(),item['properties']['datetime'],item,shape)
                 for item,shape in candidates]
        gain,_,item,shape=max(choices,key=lambda x:(x[0],x[1]))
        if gain <= boundary_wgs84.GetArea()*1e-6: break
        selected.append({'id':item['id'],'datetime':item['properties']['datetime'],
            'path':item['properties'].get('path'),'row':item['properties'].get('row'),
            'cloud_cover':item['properties'].get('eo:cloud_cover'),
            'new_area_fraction':gain/boundary_wgs84.GetArea()})
        remaining=remaining.Difference(shape)
        candidates=[pair for pair in candidates if pair[0]['id']!=item['id']]
        if feedback: feedback.setProgress(100*(1-remaining.GetArea()/boundary_wgs84.GetArea()))
    return {'source':'CBERS-4A WPM L4 DN','start_year':start_year,'end_year':end_year,
        'searched_items':len(items),'intersecting_scenes':intersecting_scenes,
        'selected_scenes':selected,'geometric_union_coverage':1-remaining.GetArea()/boundary_wgs84.GetArea(),
        'cloud_mask_available':False,'warning':'Cobertura de pegada geometrica; pixels validos podem ser menores. Nao avalia nuvens.'}


@gdal.config_options(NETWORK_OPTIONS)
def download_cbers_wpm(bounds, year, scene_id, pan, output_folder, feedback=None):
    """Preserva DN; nao inventa calibracao nem mascara de nuvens ausente."""
    if len(bounds) != 4 or not all(np.isfinite(bounds)) or not (
            -180 <= bounds[0] < bounds[2] <= 180 and -90 <= bounds[1] < bounds[3] <= 90):
        raise RuntimeError("Extensao WGS84 invalida.")
    if not 2020 <= year <= date.today().year:
        raise RuntimeError("Ano CBERS deve estar entre 2020 e o ano atual.")
    folder = Path(output_folder)
    if folder.exists() and any(folder.iterdir()):
        raise RuntimeError("Escolha uma pasta nova ou vazia; arquivos existentes serao preservados.")
    from urllib.parse import urlencode
    collection = "CB4A-WPM-L4-DN-1"
    query = urlencode({"collections": collection, "bbox": ",".join(map(str, bounds)),
        "datetime": f"{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z", "limit": 100})
    scenes = _stac_features(Request("https://data.inpe.br/bdc/stac/v1/search?" + query), feedback)
    window = ogr.CreateGeometryFromWkt(
        f"POLYGON (({bounds[0]} {bounds[1]}, {bounds[2]} {bounds[1]}, "
        f"{bounds[2]} {bounds[3]}, {bounds[0]} {bounds[3]}, {bounds[0]} {bounds[1]}))")
    scenes = [item for item in scenes if item.get("geometry") and
              ogr.CreateGeometryFromJson(json.dumps(item["geometry"])).Intersects(window)]
    def coverage(item):
        return ogr.CreateGeometryFromJson(json.dumps(item['geometry'])).Intersection(window).GetArea() / window.GetArea()
    scenes = sorted(scenes, key=lambda item: (coverage(item), item["properties"]["datetime"]), reverse=True)
    if not scenes:
        raise RuntimeError("Nenhuma cena CBERS WPM L4 neste ano e recorte.")
    item = next((item for item in scenes if item["id"] == scene_id), None) if scene_id else scenes[0]
    if item is None:
        raise RuntimeError("ID nao encontrado no ano/recorte informado.")
    footprint_coverage = coverage(item)
    if not scene_id and footprint_coverage < 0.98:
        raise RuntimeError(
            f"Nenhuma cena unica cobre 98% da extensao neste ano. Melhor cena: {item['id']} "
            f"({footprint_coverage:.1%} de cobertura geometrica). Tente outro ano ou uma area menor; "
            "um mosaico de cenas adicionais e necessario. Informe esse ID apenas se aceitar cobertura parcial.")
    if feedback is not None:
        feedback.pushInfo(f"Cobertura geometrica da cena no retangulo: {footprint_coverage:.1%}; nao avalia nuvens.")
        if footprint_coverage < 0.98:
            feedback.pushWarning("ID explicito com cobertura parcial: a saida NAO preenche toda a extensao.")
    if not item["id"] or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in item["id"]):
        raise RuntimeError("Identificador CBERS invalido no catalogo.")
    keys = ("BAND1", "BAND2", "BAND3", "BAND4") + (("BAND0",) if pan else ())
    for key in keys:
        _cbers_asset_url(item, key)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "CENAS.json").write_text(json.dumps(scenes, indent=2), encoding="utf-8")
    if feedback:
        feedback.pushWarning("CBERS DN sem calibracao e sem mascara de nuvens; nao calcular NDVI/SAVI diretamente.")
        feedback.pushInfo(f"Cena unica: {item['id']}; nuvens declaradas: {item['properties'].get('eo:cloud_cover')}")
    longitude, latitude = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    zone = min(60, max(1, int((longitude + 180) // 6) + 1))
    crs = f"EPSG:{(32700 if latitude < 0 else 32600) + zone}"
    outputs = {}
    with TemporaryDirectory(prefix="cbers_wpm_") as temporary:
        for position, key in enumerate(keys):
            _check_cancel(feedback)
            resolution = 2 if key == "BAND0" else 8
            staged = Path(temporary) / f"{key}.tif"
            dataset = gdal.Warp(str(staged), _asset_path(_cbers_asset_url(item, key)),
                format="GTiff", dstSRS=crs, outputBounds=bounds, outputBoundsSRS="EPSG:4326",
                xRes=resolution, yRes=resolution, targetAlignedPixels=True, resampleAlg="near",
                srcNodata=0, dstNodata=0, multithread=True,
                creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"],
                callback=_gdal_progress(feedback))
            if dataset is None:
                raise RuntimeError(f"Falha ao baixar {key}; pasta parcial preservada.")
            has_data = False
            for row in range(0, dataset.RasterYSize, 512):
                _check_cancel(feedback)
                for column in range(0, dataset.RasterXSize, 512):
                    has_data = bool(np.any(dataset.GetRasterBand(1).ReadAsArray(column, row,
                        min(512, dataset.RasterXSize - column), min(512, dataset.RasterYSize - row))))
                    if has_data:
                        break
                if has_data:
                    break
            if not has_data:
                dataset.Close()
                raise RuntimeError(f"Cena {item['id']} sem dados no recorte ({key}). Escolha outro ID de CENAS.json.")
            dataset.SetMetadata({"SOURCE": "INPE CBERS-4A WPM L4 DN", "SCENE_ID": item["id"],
                "DATETIME": item["properties"]["datetime"], "CALIBRATION": "UNCALIBRATED_DN",
                "NATIVE_RESOLUTION_M": str(resolution), "CLOUD_MASK": "NOT_AVAILABLE"})
            dataset.Close()
            target = folder / f"{item['id']}_{key}_{resolution}m.tif"
            copyfile(staged, target)
            outputs[key] = str(target)
            if feedback:
                feedback.setProgress(100 * (position + 1) / len(keys))
    (folder / "CBERS_METODOLOGIA.json").write_text(json.dumps({
        "scene": item, "outputs": outputs, "bounds_wgs84": bounds, "crs": crs,
        "calibration": "UNCALIBRATED_DN", "indices_enabled": False,
        "footprint_coverage_fraction": footprint_coverage,
        "warning": "Sem reflectancia e mascara de nuvens. PAN 2 m nao e NDVI 2 m.",
        "reference": "https://data.inpe.br/dados/cbers-4a/"}, indent=2), encoding="utf-8")
    return str(folder)


CBERS_TOA_REFERENCE = "https://repositorio.unesp.br/bitstreams/afe83c8d-ef8d-4f4a-9b30-79dc37f7f1e3/download (PDF p.43 e 54; impressas 38 e 49)"


def _cbers_xml_calibration(xml, band_key, scene_date):
    from xml.etree import ElementTree
    root = ElementTree.fromstring(xml)
    # ElementTree suporta namespaces; nao confundir ganho eletronico hexadecimal com calibracao absoluta.
    def value(path):
        result = root.findtext(path)
        if result is None:
            raise RuntimeError(f"XML WPM sem parametro: {path}.")
        return result.strip()
    if (value('./{*}satellite/{*}name'), value('./{*}satellite/{*}number'),
            value('./{*}satellite/{*}instrument')) != ('CBERS', '4A', 'WPM'):
        raise RuntimeError("XML nao pertence ao CBERS-4A WPM.")
    acquisition = value('./{*}image/{*}timeStamp/{*}center')[:10]
    if acquisition != scene_date:
        raise RuntimeError("Data XML difere da cena; nao misture observacoes.")
    coefficient = float(value(f'./{{*}}image/{{*}}absoluteCalibrationCoefficient/{{*}}band[@name="{band_key[-1]}"]'))
    elevation = float(value('./{*}image/{*}sunPosition/{*}elevation'))
    if not np.isfinite((coefficient, elevation)).all() or coefficient <= 0 or not 0 < elevation <= 90:
        raise RuntimeError("Coeficiente absoluto ou elevacao solar invalido.")
    return coefficient, elevation


def calibrate_cbers_wpm(bands_folder, output_folder, esun=(1958., 1852., 1559., 1091.),
                       savi_l=0.5, feedback=None):
    """TOA experimental com parametros publicados; nao e reflectancia de superficie."""
    if len(esun) != 4 or not np.isfinite(esun).all() or any(v <= 0 for v in esun):
        raise RuntimeError("ESUN exige quatro numeros positivos: azul;verde;vermelho;NIR.")
    if not np.isfinite(savi_l) or not 0 <= savi_l <= 1:
        raise RuntimeError("L do SAVI deve estar entre 0 e 1.")
    folder, output = Path(bands_folder), Path(output_folder)
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("Use pasta nova/vazia para a calibracao; entradas nunca sobrescritas.")
    manifest = json.loads((folder / 'CBERS_METODOLOGIA.json').read_text(encoding='utf-8'))
    item = manifest['scene']
    if item.get('collection') != 'CB4A-WPM-L4-DN-1' or manifest.get('calibration') != 'UNCALIBRATED_DN':
        raise RuntimeError("Exige manifesto WPM L4 DN original; nao recalibrar TOA ou RGB fusionado.")
    keys = ('BAND1', 'BAND2', 'BAND3', 'BAND4')
    scene_date = item['properties']['datetime'][:10]
    day = date.fromisoformat(scene_date).timetuple().tm_yday
    distance = 1 - 0.01672 * np.cos(np.deg2rad(0.9856 * (day - 4)))
    coefficients, xmls = {}, {}
    for key, irradiance in zip(keys, esun):
        _check_cancel(feedback)
        xml_path = folder / f"{item['id']}_{key}.xml"
        if xml_path.is_file():
            xml = xml_path.read_bytes()
        else:
            with urlopen(_cbers_asset_url(item, key + '_xml'), timeout=60) as response:
                xml = response.read(1024 * 1024 + 1)
        if len(xml) > 1024 * 1024:
            raise RuntimeError("XML WPM excede limite de 1 MB.")
        coefficient, elevation = _cbers_xml_calibration(xml, key, scene_date)
        from xml.etree import ElementTree
        root = ElementTree.fromstring(xml)
        for field in ('path', 'row'):
            expected = item.get('properties', {}).get(field)
            if expected is not None and root.findtext(f'./{{*}}image/{{*}}{field}') != str(expected):
                raise RuntimeError(f"XML {key} pertence a outra orbita/ponto ({field}).")
        coefficients[key] = {'absolute_coefficient': coefficient, 'solar_elevation_deg': elevation,
            'esun': float(irradiance), 'scale_toa': float(np.pi * coefficient * distance**2 /
                (irradiance * np.sin(np.deg2rad(elevation))))}
        xmls[key] = xml
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    with TemporaryDirectory(prefix='cbers_toa_') as temporary, ExitStack() as stack:
        inputs = {}
        for key in keys:
            path = Path(manifest['outputs'][key]).resolve()
            if path.parent != folder.resolve():
                raise RuntimeError("Banda deve estar dentro da pasta do manifesto.")
            ds = stack.enter_context(gdal.Open(str(path)))
            if ds is None or ds.RasterCount != 1 or ds.GetMetadataItem('SCENE_ID') != item['id'] or ds.GetMetadataItem('CALIBRATION') != 'UNCALIBRATED_DN':
                raise RuntimeError("Banda invalida, sem identidade/calibracao DN original.")
            inputs[key] = ds
        reference = inputs['BAND3']
        grid = (reference.RasterXSize, reference.RasterYSize, reference.GetGeoTransform(), reference.GetProjection())
        if not np.isclose(abs(grid[2][1]), 8) or not np.isclose(abs(grid[2][5]), 8):
            raise RuntimeError("Bandas WPM devem possuir pixels de 8 m.")
        for ds in inputs.values():
            if (ds.RasterXSize, ds.RasterYSize, ds.GetGeoTransform(), ds.GetProjection()) != grid:
                raise RuntimeError("Bandas nao compartilham a mesma grade; alinhe antes de calcular indices.")
        targets = {}
        products = (*keys, 'ndvi', 'savi', 'ndwi')
        for product in products:
            path = Path(temporary) / f'{product}_TOA.tif'
            ds = stack.enter_context(gdal.GetDriverByName('GTiff').Create(str(path), grid[0], grid[1], 1,
                gdal.GDT_Float32, options=['TILED=YES', 'COMPRESS=DEFLATE', 'PREDICTOR=3']))
            ds.SetGeoTransform(grid[2]); ds.SetProjection(grid[3])
            ds.GetRasterBand(1).SetNoDataValue(-9999)
            ds.SetMetadata({'CALIBRATION': 'TOA_EXPERIMENTAL', 'ATMOSPHERIC_CORRECTION': 'NONE',
                'CLOUD_MASK': 'NOT_AVAILABLE', 'SCENE_ID': item['id'], 'DATETIME': item['properties']['datetime'],
                'PRODUCT': product.upper(), 'METHOD_REFERENCE': CBERS_TOA_REFERENCE, 'SAVI_L': str(savi_l),
                'METHOD_VERSION': VERSION, 'WARNING': 'TOA nao e superficie; nuvens/sombras exigem revisao.'})
            targets[product] = (ds, path)
        valid_counts = dict.fromkeys(products, 0)
        for row in range(0, grid[1], 512):
            _check_cancel(feedback)
            rows = min(512, grid[1] - row)
            values, masks = {}, {}
            for key, ds in inputs.items():
                dn = ds.GetRasterBand(1).ReadAsArray(0, row, grid[0], rows)
                # WPM tem quantizacao de 10 bits. Excluir preenchimento e extremo saturado.
                masks[key] = np.isfinite(dn) & (dn > 0) & (dn < 1023)
                values[key] = dn.astype(np.float32) * coefficients[key]['scale_toa']
            nir, red, green = values['BAND4'], values['BAND3'], values['BAND2']
            for product in products:
                if product in keys:
                    valid, data = masks[product], values[product]
                else:
                    a, b = (green, nir) if product == 'ndwi' else (nir, red)
                    pair = ('BAND2', 'BAND4') if product == 'ndwi' else ('BAND4', 'BAND3')
                    denominator = a + b + (savi_l if product == 'savi' else 0)
                    valid = masks[pair[0]] & masks[pair[1]] & (np.abs(denominator) > 1e-12)
                    data = np.divide((a - b) * (1 + savi_l if product == 'savi' else 1),
                        denominator, out=np.zeros_like(a), where=valid)
                valid_counts[product] += int(valid.sum())
                targets[product][0].GetRasterBand(1).WriteArray(np.where(valid, data, -9999), 0, row)
            if feedback:
                feedback.setProgress(100 * (row + rows) / grid[1])
        if any(not count for count in valid_counts.values()):
            raise RuntimeError("Produto TOA sem pixels validos; nao publicar rasters vazios.")
        for ds, path in targets.values():
            ds.FlushCache()
        stack.close()
        for product, (ds, path) in targets.items():
            if product not in keys:
                _classify_product(path, product, 'jenks', 5, feedback=feedback)
            final = _commit_output(path, output / path.name, product)
            if path.with_suffix('.qml').is_file():
                copyfile(path.with_suffix('.qml'), final.with_suffix('.qml'))
            results[product] = str(final)
    for key, xml in xmls.items():
        (output / f'{item["id"]}_{key}.xml').write_bytes(xml)
    report = {'scene_id': item['id'], 'date': scene_date, 'calibration': 'TOA_EXPERIMENTAL',
        'atmospheric_correction': None, 'cloud_mask': None, 'earth_sun_distance_au': float(distance),
        'distance_method': '1 - 0.01672*cos(0.9856*(day_of_year-4)); aproximacao anual',
        'coefficients': coefficients, 'savi_l': savi_l, 'outputs': results, 'valid_pixels': valid_counts,
        'resolution_m': 8, 'classification': 'Jenks 5 classes relativas; QML nao altera pixels',
        'reference': CBERS_TOA_REFERENCE, 'warning': 'Sem validacao de campo/atmosferica. Nao comparar diretamente com SR.'}
    (output / 'CALIBRACAO_TOA.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return results


def mosaic_cbers_toa(folders, output_folder, mask=None, feedback=None):
    """Primeira observacao valida comum nas quatro bandas; nunca mistura bandas de cenas."""
    folders = [Path(p) for p in folders]
    if len(folders) < 2 or len(set(p.resolve() for p in folders)) != len(folders):
        raise RuntimeError('Informe pelo menos duas pastas TOA distintas, em ordem de prioridade.')
    output = Path(output_folder)
    if output.exists() and any(output.iterdir()):
        raise RuntimeError('Pasta de saida deve ser nova/vazia.')
    reports = [json.loads((p / 'CALIBRACAO_TOA.json').read_text(encoding='utf-8')) for p in folders]
    if any(r.get('calibration') != 'TOA_EXPERIMENTAL' for r in reports):
        raise RuntimeError('Mosaico exige cenas calibradas TOA, nao DN ou RGB.')
    if len({r['scene_id'] for r in reports}) != len(reports):
        raise RuntimeError('Ha cenas repetidas; use observacoes distintas.')
    if any(r['savi_l'] != reports[0]['savi_l'] for r in reports):
        raise RuntimeError('Use o mesmo L do SAVI em todas as cenas.')
    if any([r['coefficients'][k]['esun'] for k in ('BAND1','BAND2','BAND3','BAND4')] !=
           [reports[0]['coefficients'][k]['esun'] for k in ('BAND1','BAND2','BAND3','BAND4')] for r in reports):
        raise RuntimeError('Use o mesmo conjunto ESUN em todas as cenas.')
    products = ('BAND1','BAND2','BAND3','BAND4','ndvi','savi','ndwi')
    with ExitStack() as stack, TemporaryDirectory(prefix='cbers_mosaic_') as temporary:
        all_inputs = []
        for folder, report in zip(folders, reports):
            inputs = {}
            for key in products:
                path = Path(report['outputs'][key]).resolve()
                if path.parent != folder.resolve():
                    raise RuntimeError('Arquivo TOA deve estar dentro da pasta informada.')
                ds = stack.enter_context(gdal.Open(str(path)))
                if ds is None or ds.GetMetadataItem('CALIBRATION') != 'TOA_EXPERIMENTAL' or ds.GetMetadataItem('SCENE_ID') != report['scene_id']:
                    raise RuntimeError('Banda sem calibracao TOA identificada.')
                inputs[key] = ds
            all_inputs.append(inputs)
        ref = all_inputs[0]['BAND3']; crs = ref.GetProjection()
        extents = []
        for inputs in all_inputs:
            ds = inputs['BAND3']; gt=ds.GetGeoTransform()
            srs = osr.SpatialReference(wkt=ds.GetProjection())
            if not srs.IsProjected() or not np.isclose(srs.GetLinearUnits(),1) or ds.GetProjection() != crs or gt[2] or gt[4] or gt[1] != 8 or gt[5] != -8:
                raise RuntimeError('Todas as cenas devem estar na mesma UTM, sem rotacao, a 8 m.')
            if not np.allclose(((gt[0]-ref.GetGeoTransform()[0])/8 % 1,
                                (gt[3]-ref.GetGeoTransform()[3])/8 % 1),0):
                raise RuntimeError('Grades nao alinhadas em pixels de 8 m.')
            for other in inputs.values():
                if (other.GetGeoTransform(),other.RasterXSize,other.RasterYSize) != (gt,ds.RasterXSize,ds.RasterYSize):
                    raise RuntimeError('Produtos de uma cena possuem grades diferentes.')
            extents.append((gt[0],gt[3]-ds.RasterYSize*8,gt[0]+ds.RasterXSize*8,gt[3]))
        xmin=min(e[0] for e in extents);ymin=min(e[1] for e in extents)
        xmax=max(e[2] for e in extents);ymax=max(e[3] for e in extents)
        width=int(round((xmax-xmin)/8));height=int(round((ymax-ymin)/8))
        output.mkdir(parents=True,exist_ok=True)
        targets={}
        for key in (*products,'SCENE_INDEX'):
            path=Path(temporary)/(key+'_mosaico_TOA.tif')
            ds=stack.enter_context(gdal.GetDriverByName('GTiff').Create(str(path),width,height,1,
                gdal.GDT_Float32,options=['TILED=YES','COMPRESS=DEFLATE','BIGTIFF=IF_SAFER']))
            ds.SetGeoTransform((xmin,8,0,ymax,0,-8));ds.SetProjection(crs)
            ds.GetRasterBand(1).SetNoDataValue(-9999)
            ds.SetMetadata({'CALIBRATION':'TOA_EXPERIMENTAL','ATMOSPHERIC_CORRECTION':'NONE',
                'CLOUD_MASK':'NOT_AVAILABLE','COHERENT_OBSERVATIONS':'YES','PRODUCT':key,
                'SCENES':json.dumps([r['scene_id'] for r in reports]),'SAVI_L':str(reports[0]['savi_l'])})
            targets[key]=(ds,path)
        counts=[0]*len(reports)
        # ponytail: blocos 512x512 limitam memoria; prioridade manual, sem QA de nuvens disponivel.
        for row in range(0,height,512):
            for col in range(0,width,512):
                _check_cancel(feedback)
                h,w=min(512,height-row),min(512,width-col)
                values={key:np.full((h,w),-9999,dtype=np.float32) for key in targets}
                filled=np.zeros((h,w),dtype=bool)
                for index,(inputs,extent) in enumerate(zip(all_inputs,extents)):
                    ox=int(round((extent[0]-xmin)/8));oy=int(round((ymax-extent[3])/8))
                    x0,y0=max(col,ox),max(row,oy)
                    x1,y1=min(col+w,ox+inputs['BAND3'].RasterXSize),min(row+h,oy+inputs['BAND3'].RasterYSize)
                    if x1<=x0 or y1<=y0: continue
                    dest=np.s_[y0-row:y1-row,x0-col:x1-col]
                    arrays={key:ds.GetRasterBand(1).ReadAsArray(x0-ox,y0-oy,x1-x0,y1-y0) for key,ds in inputs.items()}
                    valid=~filled[dest]
                    for array in arrays.values(): valid &= np.isfinite(array)&(array!=-9999)
                    for key,array in arrays.items(): values[key][dest][valid]=array[valid]
                    values['SCENE_INDEX'][dest][valid]=index+1
                    filled[dest][valid]=True;counts[index]+=int(valid.sum())
                for key,(ds,path) in targets.items(): ds.GetRasterBand(1).WriteArray(values[key],col,row)
            if feedback: feedback.setProgress(100*(row+h)/height)
        if not sum(counts): raise RuntimeError('Mosaico sem dados validos.')
        for ds,path in targets.values(): ds.FlushCache()
        stack.close()
        result={}
        for key,(ds,path) in targets.items():
            prepared=_prepare_product_grid(path,Path(temporary),key,mask=mask,feedback=feedback) if mask else path
            if key in ('ndvi','savi','ndwi'): _classify_product(prepared,key,'jenks',5,feedback=feedback)
            final=_commit_output(prepared,output/path.name,key)
            if Path(prepared).with_suffix('.qml').is_file(): copyfile(Path(prepared).with_suffix('.qml'),final.with_suffix('.qml'))
            result[key]=str(final)
        report={'calibration':'TOA_EXPERIMENTAL','scenes':reports,'outputs':result,
            'priority':'ordem das pastas, primeira observacao valida nas quatro bandas e indices',
            'pixels_per_scene_before_clip':counts,'mask':mask,'cloud_mask':None,
            'warning':'Multitemporal sem QA de nuvens; lacunas permanecem NoData. Nao e reflectancia de superficie.'}
        if mask:
            with gdal.Open(result['ndvi']) as ds:
                report['coverage_inside_polygon']=ds.GetMetadataItem('CLIP_VALID_FRACTION')
        else: report['coverage_in_rectangle']=sum(counts)/(width*height)
        (output/'MOSAICO_CBers_TOA.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return result


def _planetary_computer_token(collection):
    try:
        with urlopen(f"{PLANETARY_COMPUTER_SAS_URL}/{collection}", timeout=30) as response:
            return json.load(response)["token"]
    except Exception as exc:
        raise RuntimeError(f"Falha ao autorizar o download gratuito Landsat: {exc}") from exc


def _asset_path(href, token=None):
    return f"/vsicurl/{_authorized_asset_url(href, token)}"


def _band_calibration(source, asset_key, asset=None):
    if source.startswith("hls_"):
        return (None, None) if asset_key == "fmask" else (0.0001, 0.0)
    if source == "l2a" and "sentinel-s2-l2a-cogs/" in (asset or {}).get("href", ""):
        raise RuntimeError("COG Sentinel legado com calibracao ambigua; use sentinel-2-c1-l2a.")
    bands = (asset or {}).get("raster:bands", [])
    if bands and "scale" in bands[0]:
        return float(bands[0]["scale"]), float(bands[0].get("offset", 0.0))
    if source == "l2a":
        spectral = {
            "coastal", "blue", "green", "red", "rededge1", "rededge2",
            "rededge3", "nir", "nir08", "nir09", "swir16", "swir22",
        }
        if asset_key in spectral:
            raise RuntimeError("Sentinel-2 sem escala/offset no asset STAC; calibracao ambigua.")
        return None, None
    if source == "drone":
        return 1.0, 0.0
    if asset_key in {"coastal", "blue", "green", "red", "nir08", "swir16", "swir22"}:
        return 0.0000275, -0.2
    if asset_key in {"lwir", "lwir11"}:
        return 0.00341802, 149.0
    return None, None


def _spectral_index_spec(source, index):
    if index not in SPECTRAL_INDICES.get(source, ()):
        title = DRONE_SOURCE["title"] if source == "drone" else MULTIBAND_SOURCES[source]["title"]
        raise RuntimeError(f"Índice {index.upper()} indisponível para {title}.")
    nir = "nir" if source in {"l2a", "drone"} else "nir08"
    thermal = "lwir" if source in {"landsat45", "landsat7"} else "lwir11"
    return {
        "ndvi": ("NDVI", (nir, "red"), "(NIR - Red) / (NIR + Red)", "adimensional"),
        "ndwi": ("NDWI", ("green", nir), "(Green - NIR) / (Green + NIR)", "adimensional"),
        "ndbi": ("NDBI", ("swir16", nir), "(SWIR1 - NIR) / (SWIR1 + NIR)", "adimensional"),
        "savi": ("SAVI", (nir, "red"), "1,5 × (NIR - Red) / (NIR + Red + 0,5)", "adimensional"),
        "evi2": ("EVI2", (nir, "red"), "2.5 * (NIR - Red) / (NIR + 2.4 * Red + 1)", "adimensional"),
        "vari": ("VARI", ("green", "red", "blue"), "(Green - Red) / (Green + Red - Blue)", "adimensional"),
        "lst": ("LST", (thermal,), "DN × 0,00341802 + 149 - 273,15", "°C"),
    }[index]


def _tms_connection_string(url_template, zoom):
    """Converte um template {z}/{x}/{y} num minidriver GDAL_WMS (TMS).

    O GDAL_WMS/TMS busca os blocos via libcurl com suporte a HTTP/2
    multiplexado (configurado em run()), muito mais rápido do que o
    provedor XYZ/WMS do próprio QGIS para leitura em lote de milhares
    de tiles pequenos.
    """
    server_url = (
        url_template.replace("{z}", "${z}")
        .replace("{x}", "${x}")
        .replace("{y}", "${y}")
        .replace("&", "&amp;")
    )
    return (
        "<GDAL_WMS>"
        f'<Service name="TMS"><ServerUrl>{server_url}</ServerUrl></Service>'
        "<DataWindow>"
        f"<UpperLeftX>-{WEB_MERCATOR_WORLD_EXTENT}</UpperLeftX>"
        f"<UpperLeftY>{WEB_MERCATOR_WORLD_EXTENT}</UpperLeftY>"
        f"<LowerRightX>{WEB_MERCATOR_WORLD_EXTENT}</LowerRightX>"
        f"<LowerRightY>-{WEB_MERCATOR_WORLD_EXTENT}</LowerRightY>"
        f"<TileLevel>{zoom}</TileLevel>"
        "<TileCountX>1</TileCountX><TileCountY>1</TileCountY>"
        "<YOrigin>top</YOrigin>"
        "</DataWindow>"
        "<Projection>EPSG:3857</Projection>"
        "<BlockSizeX>256</BlockSizeX><BlockSizeY>256</BlockSizeY>"
        "<BandsCount>3</BandsCount>"
        "<Cache/>"
        "</GDAL_WMS>"
    )


def _virtual_earth_connection(zoom):
    """Monta o minidriver GDAL para URLs Bing baseadas em quadkey."""
    return (
        "<GDAL_WMS>"
        f'<Service name="VirtualEarth"><ServerUrl>{BING_URL_TEMPLATE}</ServerUrl></Service>'
        "<DataWindow>"
        f"<UpperLeftX>-{WEB_MERCATOR_WORLD_EXTENT}</UpperLeftX>"
        f"<UpperLeftY>{WEB_MERCATOR_WORLD_EXTENT}</UpperLeftY>"
        f"<LowerRightX>{WEB_MERCATOR_WORLD_EXTENT}</LowerRightX>"
        f"<LowerRightY>-{WEB_MERCATOR_WORLD_EXTENT}</LowerRightY>"
        f"<TileLevel>{zoom}</TileLevel>"
        "<TileCountX>1</TileCountX><TileCountY>1</TileCountY>"
        "</DataWindow>"
        "<Projection>EPSG:3857</Projection>"
        "<BlockSizeX>256</BlockSizeX><BlockSizeY>256</BlockSizeY>"
        "<BandsCount>3</BandsCount><Cache/>"
        "</GDAL_WMS>"
    )




def _write_empty_tile(extent, columns, rows, path):
    """Cria tile transparente quando o servidor não possui cobertura."""
    xmin, ymin, xmax, ymax = extent
    path.unlink(missing_ok=True)
    driver = gdal.GetDriverByName("GTiff")
    dataset = driver.Create(
        str(path),
        columns,
        rows,
        3,
        gdal.GDT_Byte,
        options=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"],
    )
    dataset.SetGeoTransform(
        (
            xmin,
            (xmax - xmin) / columns,
            0,
            ymax,
            0,
            -(ymax - ymin) / rows,
        )
    )
    dataset.SetProjection(QgsCoordinateReferenceSystem("EPSG:3857").toWkt())
    for band_number in range(1, 4):
        band = dataset.GetRasterBand(band_number)
        band.Fill(0)
        band.SetNoDataValue(0)
    dataset = None


def _is_unavailable_tile(path):
    """Reconhece a imagem cinza de erro devolvida por serviços de tiles."""
    dataset = gdal.Open(str(path))
    if dataset is None or dataset.RasterCount < 3:
        return True
    sample = dataset.ReadAsArray(
        0,
        0,
        dataset.RasterXSize,
        dataset.RasterYSize,
        buf_xsize=64,
        buf_ysize=64,
    )
    dataset = None
    rgb = sample[:3].astype(np.int16)
    gray = rgb.max(axis=0) - rgb.min(axis=0) <= 2
    error_gray = gray & (rgb.mean(axis=0) >= 195) & (rgb.mean(axis=0) <= 210)
    return float(error_gray.mean()) >= 0.90


@gdal.config_options(NETWORK_OPTIONS)
def _fetch_tms_window(connection, extent, columns, rows, path, feedback=None):
    """Busca uma janela georreferenciada do minidriver TMS via GDAL, com nova
    tentativa e espera exponencial.
    foram definidos globalmente uma única vez antes da chamada em paralelo —
    ver o comentário em run() sobre por que isso evita condição de corrida.
    """
    xmin, ymin, xmax, ymax = extent
    last_error = None
    for attempt in range(RETRIES):
        _check_cancel(feedback)
        path.unlink(missing_ok=True)
        dataset = None
        try:
            dataset = gdal.Translate(
                str(path),
                connection,
                options=gdal.TranslateOptions(
                    format="GTiff",
                    projWin=[xmin, ymax, xmax, ymin],
                    width=columns,
                    height=rows,
                    outputType=gdal.GDT_Byte,
                    resampleAlg="near",
                    creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"],
                    callback=_gdal_progress(feedback),
                ),
            )
        except RuntimeError as exc:
            last_error = exc
            if "401" in str(exc) or "403" in str(exc):
                raise RuntimeError("Falha de autenticacao/permissao HTTP 401/403; verifique a chave e o servico.") from None
        if dataset is not None:
            dataset.Close()
            dataset = None
            if not _is_unavailable_tile(path):
                return True
        if attempt + 1 < RETRIES:
            sleep(2**attempt)
    _check_cancel(feedback)
    if last_error is not None and "404" not in str(last_error):
        raise RuntimeError("Falha de rede ao baixar tiles apos as tentativas; nenhuma imagem vazia foi aceita.") from None
    _write_empty_tile(extent, columns, rows, path)
    return False


def _download_chunks(connection, parts, resolution, temporary, feedback=None):
    """Baixa os fragmentos em paralelo (GDAL/libcurl), com ETA e cancelamento."""
    cancelled = threading.Event()
    lock = threading.Lock()
    state = {"downloaded": 0, "completed": 0, "empty": 0, "samples": []}
    chunks = [None] * len(parts)
    sample_limit = max(1, min(20, len(parts)))
    fixed_total = None
    started = monotonic()
    first_error = None
    last_log_milestone = -1
    last_completed = -1

    def worker(index, part):
        if cancelled.is_set():
            return index, None
        xmin, ymin, xmax, ymax = part
        columns = max(1, ceil((xmax - xmin) / resolution))
        rows = max(1, ceil((ymax - ymin) / resolution))
        path = temporary / f"parte_{index:04d}.tif"
        available = _fetch_tms_window(connection, part, columns, rows, path, feedback)
        size = path.stat().st_size
        with lock:
            state["downloaded"] += size
            state["completed"] += 1
            state["empty"] += int(not available)
            if len(state["samples"]) < sample_limit:
                state["samples"].append(size)
        return index, str(path)

    with ThreadPoolExecutor(max_workers=CHUNK_WORKERS) as executor:
        pending = {
            executor.submit(worker, index, part): index
            for index, part in enumerate(parts)
        }
        while pending:
            if feedback is not None and feedback.isCanceled():
                cancelled.set()
            if cancelled.is_set() and first_error is None:
                for future in pending:
                    future.cancel()
                break

            done_now = {future for future in pending if future.done()}
            for future in done_now:
                pending.pop(future, None)
                try:
                    index, path_str = future.result()
                    if path_str is not None:
                        chunks[index] = path_str
                except Exception as exc:
                    first_error = first_error or exc
                    cancelled.set()

            with lock:
                downloaded = state["downloaded"]
                completed = state["completed"]
                samples = list(state["samples"])
            if samples and completed != last_completed:
                last_completed = completed
                current_estimate = int(sum(samples) / len(samples) * len(parts))
                if fixed_total is None and len(samples) >= sample_limit:
                    fixed_total = current_estimate
                estimated_total = fixed_total or current_estimate
                elapsed = max(monotonic() - started, 0.001)
                speed = downloaded / elapsed
                eta = max(estimated_total - downloaded, 0) / speed if speed > 0 else 0
                shown_total = max(estimated_total, downloaded, 1)
                progress_text = (
                    f"{completed}/{len(parts)} fragmentos — "
                    f"{downloaded / 1024**2:.1f} MB de {shown_total / 1024**3:.2f} GB — "
                    f"tempo restante: {_format_eta(eta)}"
                )
                if feedback is not None:
                    feedback.setProgress(completed * 70 / len(parts))
                    feedback.setProgressText(progress_text)
                    milestone = completed * 10 // len(parts)
                    if milestone > last_log_milestone:
                        feedback.pushInfo(f"Download: {milestone * 10}% ({completed}/{len(parts)} fragmentos).")
                        last_log_milestone = milestone
                else:
                    print(progress_text)
            sleep(0.1)

    if first_error is not None:
        raise first_error
    if cancelled.is_set():
        if feedback is not None:
            feedback.pushWarning("Download cancelado; os arquivos temporários serão removidos.")
        return None
    if feedback is not None:
        if last_log_milestone < 10:
            feedback.pushInfo("Download: 100%.")
        if state["empty"]:
            feedback.pushWarning(f"{state['empty']} fragmento(s) sem cobertura foram preenchidos como transparência.")
        feedback.setProgress(70)
    return chunks, state["empty"]




def _embed_metadata(dataset, values, band_name=None):
    clean = {key: str(value) for key, value in values.items() if value not in (None, "")}
    clean.update(SOFTWARE_AUTHOR="Raphael S. / Radkzin", CODE_REPOSITORY="https://github.com/Radkzin/satelite-cadente",
                 SOFTWARE_LICENSE="GPL-3.0")
    dataset.SetMetadata(clean)
    dataset.SetMetadataItem("TIFFTAG_DOCUMENTNAME", clean.get("TITLE", ""))
    dataset.SetMetadataItem("TIFFTAG_IMAGEDESCRIPTION", clean.get("ABSTRACT", ""))
    dataset.SetMetadataItem("TIFFTAG_SOFTWARE", "QGIS + GDAL; satellite_mosaic_qgis.py")
    dataset.SetMetadataItem("TIFFTAG_COPYRIGHT", clean.get("RIGHTS", ""))
    if band_name:
        dataset.GetRasterBand(1).SetDescription(band_name)


def _apply_layer_metadata(layer):
    layer.setScaleBasedVisibility(False)
    dataset = gdal.Open(layer.source().split("|", 1)[0])
    values = dataset.GetMetadata() if dataset else {}
    dataset = None
    metadata = layer.metadata()
    metadata.setIdentifier(values.get("IDENTIFIER", Path(layer.source()).stem))
    metadata.setTitle(values.get("TITLE", layer.name()))
    metadata.setAbstract("\n".join(values[key] if key == "ABSTRACT" else f"{key}: {values[key]}"
        for key in ("ABSTRACT", "SOURCE", "SOURCE_URL", "INPUT_BANDS", "ACQUISITION_DATES", "ACQUISITION_GRANULE",
                    "METHODOLOGY", "METHOD_REFERENCE", "PROCESSING", "CRS", "OUTPUT_RESOLUTION_M", "VALID_FRACTION",
                    "NODATA_NOTE", "CODE_REPOSITORY") if values.get(key)))
    metadata.setLanguage("pt-BR")
    metadata.setType("dataset")
    metadata.setKeywords({"tema": values.get("KEYWORDS", "imagem de satélite").split(";")})
    metadata.setRights([values["RIGHTS"]] if values.get("RIGHTS") else [])
    metadata.setLicenses([values["LICENSE"]] if values.get("LICENSE") else [])
    metadata.setHistory([values["PROCESSING"]] if values.get("PROCESSING") else [])
    metadata.setCrs(layer.crs())
    layer.setMetadata(metadata)


def _create_spectral_index(source, index, outputs_by_asset, bounds_web, temporary, output_directory, feedback=None,
                           savi_l=0.5):
    if index == "savi" and (isinstance(savi_l, bool) or not isinstance(savi_l, (int, float)) or not np.isfinite(savi_l) or not 0 <= savi_l <= 1):
        raise RuntimeError("Fator L do SAVI deve ser finito entre 0 e 1.")
    label, asset_keys, formula, units = _spectral_index_spec(source, index)
    if index == "savi":
        formula = f"(1 + {savi_l:g}) * (NIR - Red) / (NIR + Red + {savi_l:g})"
    try:
        resolution = max(outputs_by_asset[key][0] for key in asset_keys)
    except KeyError as exc:
        raise RuntimeError(f"A banda necessária para {label} não está disponível nas cenas selecionadas: {exc}.") from exc

    output_path = _product_output_path(output_directory / f"{label}_{resolution}m.tif", index)
    with ExitStack() as stack:
        originals = {key: stack.enter_context(gdal.Open(str(outputs_by_asset[key][1]))) for key in asset_keys}
        reference_key = max(asset_keys, key=lambda key: outputs_by_asset[key][0])
        reference = originals[reference_key]
        crs = reference.GetProjection()
        columns, rows = reference.RasterXSize, reference.RasterYSize
        aligned = {}
        calibrations = {}
        for asset_key, original in originals.items():
            _check_cancel(feedback)
            band = original.GetRasterBand(1)
            scale, offset = band.GetScale(), band.GetOffset()
            if scale is None or offset is None:
                metadata = original.GetMetadata()
                try:
                    scale, offset = float(metadata["SCALE_FACTOR"]), float(metadata["ADDITIVE_OFFSET"])
                except (KeyError, ValueError):
                    scale, offset = _band_calibration(source, asset_key)
            calibrations[asset_key] = (scale or 1.0, offset or 0.0)
            if (original.RasterXSize == columns and original.RasterYSize == rows
                    and original.GetGeoTransform() == reference.GetGeoTransform()
                    and original.GetSpatialRef().IsSame(reference.GetSpatialRef())
                    and band.GetNoDataValue() is not None):
                aligned[asset_key] = original
                continue
            aligned[asset_key] = stack.enter_context(gdal.Warp(
                str(temporary / f"{label}_{asset_key}.tif"), original,
                options=gdal.WarpOptions(
                    format="GTiff", dstSRS=crs, outputBounds=_raster_bounds(outputs_by_asset[reference_key][1]),
                    width=columns, height=rows, resampleAlg="near", srcNodata=band.GetNoDataValue(),
                    dstNodata=_asset_nodata(source, asset_key), overviewLevel="NONE",
                    creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2"],
                    callback=_gdal_progress(feedback),
                ),
            ))
        output = stack.enter_context(gdal.GetDriverByName("GTiff").Create(
            str(output_path), columns, rows, 1, gdal.GDT_Float32,
            options=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=IF_SAFER"],
        ))
        output.SetGeoTransform(reference.GetGeoTransform())
        output.SetProjection(crs)
        output_band = output.GetRasterBand(1)
        output_band.SetNoDataValue(-9999.0)
        valid_pixels = 0
        for row in range(0, rows, 512):
            _check_cancel(feedback)
            height = min(512, rows - row)
            for column in range(0, columns, 512):
                width = min(512, columns - column)
                values = {}
                valid = np.ones((height, width), dtype=bool)
                for asset_key, dataset in aligned.items():
                    raw = dataset.GetRasterBand(1).ReadAsArray(column, row, width, height)
                    valid &= (raw != dataset.GetRasterBand(1).GetNoDataValue()) & np.isfinite(raw)
                    scale, offset = calibrations[asset_key]
                    values[asset_key] = raw.astype(np.float32) * scale + offset
                result = np.full((height, width), -9999.0, dtype=np.float32)
                with np.errstate(divide="ignore", invalid="ignore"):
                    if index == "lst":
                        result[valid] = values[asset_keys[0]][valid] - 273.15
                    elif index == "vari":
                        green, red, blue = (values[key] for key in asset_keys)
                        denominator = green + red - blue
                        good = valid & (green >= 0) & (red >= 0) & (blue >= 0) & (np.abs(denominator) > 1e-8)
                        result[good] = (green[good] - red[good]) / denominator[good]
                    else:
                        first, second = values[asset_keys[0]], values[asset_keys[1]]
                        denominator = (first + 2.4 * second + 1.0 if index == "evi2" else
                                       first + second + (savi_l if index == "savi" else 0.0))
                        good = valid & (first >= 0) & (second >= 0) & (np.abs(denominator) > 1e-8)
                        gain = 2.5 if index == "evi2" else 1 + savi_l if index == "savi" else 1.0
                        result[good] = gain * (first[good] - second[good]) / denominator[good]
                result[~np.isfinite(result)] = -9999.0
                output_band.WriteArray(result, column, row)
                valid_pixels += int(np.count_nonzero(result != -9999.0))
        output_band.FlushCache()
        output_band = None
        if not valid_pixels:
            raise RuntimeError(f"Nao ha pixels validos para {label}; verifique a cobertura das bandas necessarias.")
        coverage = valid_pixels / (columns * rows)
        missing_pixels = columns * rows - valid_pixels
        if feedback is not None:
            feedback.pushInfo(f"Cobertura {label}: {coverage:.2%} de pixels validos na grade do produto.")
            if coverage < 0.98:
                note = LST_NODATA_NOTE if index == "lst" else "Ausencias nas bandas e filtros de qualidade/calibracao permanecem NoData."
                feedback.pushWarning(f"{label}: {1 - coverage:.2%} ({missing_pixels}) pixels sem dados. {note}")
        _embed_metadata(output,
        {
            "IDENTIFIER": output_path.stem,
            "TITLE": f"{label} — {(DRONE_SOURCE if source == 'drone' else MULTIBAND_SOURCES[source])['title']}",
            "ABSTRACT": f"{label} calculado após mosaico e recorte; valores -9999 representam NoData.",
            "KEYWORDS": f"{label};índice espectral;{(DRONE_SOURCE if source == 'drone' else MULTIBAND_SOURCES[source])['title']}",
            "INDEX": label,
            "INDEX_FORMULA": formula,
            "INDEX_UNIT": units,
            "INPUT_BANDS": ",".join(asset_keys),
            "OUTPUT_RESOLUTION_M": resolution,
            "CRS": reference.GetSpatialRef().GetAuthorityCode(None) or crs,
            "NODATA_VALUE": -9999,
            "VALID_FRACTION": coverage,
            "MISSING_PIXELS": missing_pixels,
            "METHOD_VERSION": VERSION,
            **({"NODATA_NOTE": LST_NODATA_NOTE} if index == "lst" else {}),
            "METHODOLOGY": (f"Ortomosaico local georreferenciado; bandas alinhadas na mesma grade. Índice: {formula}."
                            if source == "drone" else f"{MULTIBAND_METHODOLOGY} Índice: {formula}."),
            "METHOD_REFERENCE": METHOD_REFERENCES[index],
            **({"SAVI_L": savi_l} if index == "savi" else {}),
            "PROCESSING": f"Bandas alinhadas em {resolution} m e índice calculado por {formula}.",
            "RIGHTS": (DRONE_SOURCE if source == "drone" else MULTIBAND_SOURCES[source])["rights"],
            "LICENSE": (DRONE_SOURCE if source == "drone" else MULTIBAND_SOURCES[source])["license"],
        },
            f"{label} ({units})",
        )
        _build_overviews(output, feedback=feedback)
    return label, resolution, output_path, formula


def _tsharp_scene(ndvi, temperature, scene_mask, factor, fill_gaps=False, min_r2=0.2, feedback=None):
    """TsHARP por observacao; conserva media de Kelvin^4 onde ha suporte termico."""
    _check_cancel(feedback)
    if ndvi.shape != temperature.shape or ndvi.shape != scene_mask.shape or ndvi.ndim != 2:
        raise RuntimeError("As grades NDVI, LST e cena precisam coincidir.")
    if factor not in (2, 4) or not np.isfinite(min_r2) or not 0 <= min_r2 <= 1:
        raise RuntimeError("Parametros de fusao invalidos.")
    measured = scene_mask & np.isfinite(temperature) & (temperature != -9999)
    land = scene_mask & np.isfinite(ndvi) & (ndvi > 0) & (ndvi <= 1)
    result = np.where(measured, temperature, -9999).astype(np.float64)
    flags = measured.astype(np.uint8)
    report = {"status": "skipped", "support_pixels": factor, "validation": "holdout espacial; nao valida a resolucao fina"}
    rows, columns = ndvi.shape
    padding = ((0, -rows % factor), (0, -columns % factor))
    nr, nc = ceil(rows / factor), ceil(columns / factor)

    def blocks(values):
        return np.pad(values, padding, constant_values=np.nan).reshape(nr, factor, nc, factor).transpose(0, 2, 1, 3)

    def expand(values):
        return values.repeat(factor, 0).repeat(factor, 1)[:rows, :columns]

    paired = land & measured
    count = blocks(paired.astype(float)).sum((2, 3))
    count = np.nan_to_num(count)
    low_ndvi = np.divide(np.nansum(blocks(np.where(paired, ndvi, np.nan)), (2, 3)), count,
                         out=np.full((nr, nc), np.nan), where=count > 0)
    low_radiance = np.divide(np.nansum(blocks(np.where(paired, (temperature + 273.15)**4, np.nan)), (2, 3)), count,
                             out=np.full((nr, nc), np.nan), where=count > 0)
    low_temperature = low_radiance**0.25 - 273.15
    cells = np.flatnonzero(count.ravel() == factor * factor)
    # ponytail: ate 50000 celulas no ajuste; aumentar somente apos medir custo/memoria.
    cells = cells[::max(1, ceil(cells.size / 50000))]
    x = 1 - (1 - low_ndvi.ravel()[cells])**0.625
    y = low_temperature.ravel()[cells]
    holdout = ((cells // nc // 4 + cells % nc // 4) % 5) == 0
    if cells.size < 40 or holdout.sum() < 8 or (~holdout).sum() < 24 or np.ptp(x) < 1e-6:
        report["reason"] = "Menos de 40 celulas completas, poucos blocos de validacao ou vegetacao sem variacao."
        return result, flags, report
    design = np.column_stack((np.ones(x.size), x))
    coefficients = np.linalg.lstsq(design[~holdout], y[~holdout], rcond=None)[0]
    error = design[holdout] @ coefficients - y[holdout]
    variance = float(np.sum((y[holdout] - y[holdout].mean())**2))
    r2 = 1 - float(error @ error) / variance if variance > 1e-8 else -1.0
    report.update(validation_r2=r2, validation_rmse_c=float(np.sqrt(np.mean(error**2))),
                  validation_cells=int(holdout.sum()), fitted_cells=int(cells.size))
    if r2 < min_r2 or coefficients[1] >= 0:
        report["reason"] = "Relacao vegetacao-temperatura fraca ou nao inversa; fusao recusada nesta cena."
        return result, flags, report
    coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
    if coefficients[1] >= 0:
        report["reason"] = "Ajuste final sem relacao inversa vegetacao-temperatura."
        return result, flags, report
    land_count = np.nansum(blocks(land.astype(float)), (2, 3))
    supported = (land_count == factor * factor) & (count >= 0.8 * factor * factor)
    observed_ndvi = ndvi[paired]
    eligible = land & (ndvi >= observed_ndvi.min()) & (ndvi <= observed_ndvi.max())
    eligible &= expand(supported) | (fill_gaps & ~measured)
    if not fill_gaps:
        eligible &= measured
    basis = 1 - (1 - np.clip(ndvi, 0, 1))**0.625
    result[eligible] = coefficients[0] + coefficients[1] * basis[eligible]

    # Correcao constante por celula: media de Kelvin^4, com emissividade constante.
    predicted = blocks(eligible.astype(float)) == 1
    kelvin = blocks(np.where(result != -9999, result + 273.15, np.nan))
    fixed = np.nansum(np.where(predicted, np.nan, kelvin**4), (2, 3))
    target = low_radiance * np.isfinite(kelvin).sum((2, 3))
    shift = np.zeros((nr, nc))
    for _ in range(5):
        _check_cancel(feedback)
        values = np.where(predicted, kelvin + shift[:, :, None, None], np.nan)
        difference = np.nansum(values**4, (2, 3)) + fixed - target
        derivative = 4 * np.nansum(values**3, (2, 3))
        shift -= np.divide(difference, derivative, out=np.zeros_like(shift), where=supported & (derivative > 0))
    result[eligible] += expand(shift)[eligible]
    flags[eligible & measured] = 2
    flags[eligible & ~measured] = 3
    report.update(status="fitted", intercept_c=float(coefficients[0]), vegetation_coefficient=float(coefficients[1]),
                  sharpened_pixels=int(np.count_nonzero(flags == 2)), gap_filled_pixels=int(np.count_nonzero(flags == 3)),
                  fine_predictor_extrapolated_pixels=int(np.count_nonzero(eligible & ((basis < x.min()) | (basis > x.max())))))
    return result, flags, report


def _create_rgb_preview(
    source,
    outputs_by_asset,
    output_directory,
    asset_keys=("red", "green", "blue"),
    filename=None,
    title=None,
    method_reference=None,
):
    try:
        paths = [outputs_by_asset[key][1] for key in asset_keys]
    except KeyError:
        return None
    path = _product_output_path(output_directory / (filename or MULTIBAND_RGB_PREVIEW[source]), "rgb")
    dataset = gdal.BuildVRT(
        str(path),
        [str(item) for item in paths],
        options=gdal.BuildVRTOptions(
            separate=True,
            resolution="highest",
            srcNodata=_asset_nodata(source, asset_keys[0]),
            VRTNodata=_asset_nodata(source, asset_keys[0]),
        ),
    )
    if dataset is None:
        raise RuntimeError("Não foi possível criar a composição RGB de pré-visualização.")
    # O RGB exibe DN; a calibracao fisica continua nas bandas analiticas separadas.
    for position, color in enumerate((gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand), 1):
        band = dataset.GetRasterBand(position)
        band.SetScale(1.0)
        band.SetOffset(0.0)
        band.SetColorInterpretation(color)
    band = None
    _embed_metadata(
        dataset,
        {
            "IDENTIFIER": path.stem,
            "TITLE": title or f"Composição RGB — {MULTIBAND_SOURCES[source]['title']}",
            "ABSTRACT": "Composição visual RGB das bandas brutas, com contraste aplicado somente no QGIS.",
            "KEYWORDS": f"RGB;visualização;{MULTIBAND_SOURCES[source]['title']}",
            "SOURCE": MULTIBAND_SOURCES[source]["source"],
            "CRS": dataset.GetSpatialRef().GetAuthorityCode(None),
            "METHODOLOGY": method_reference or METHOD_REFERENCES["rgb"],
            "PROCESSING": "VRT RGB de pré-visualização; bandas brutas permanecem inalteradas.",
            "RIGHTS": MULTIBAND_SOURCES[source]["rights"],
            "LICENSE": MULTIBAND_SOURCES[source]["license"],
        },
    )
    dataset = None
    return path


def apply_rgb_preview_style(layer):
    if not isinstance(layer, QgsRasterLayer):
        return
    layer.setScaleBasedVisibility(False)
    if layer.bandCount() < 3:
        return
    provider = layer.dataProvider()
    renderer = QgsMultiBandColorRenderer(provider, 1, 2, 3)
    transparent = QgsRasterTransparency.TransparentThreeValuePixel()
    transparent.red = transparent.green = transparent.blue = provider.sourceNoDataValue(1) if provider.sourceHasNoDataValue(1) else 0
    transparent.percentTransparent = 100
    transparency = QgsRasterTransparency()
    transparency.setTransparentThreeValuePixelList([transparent])
    renderer.setRasterTransparency(transparency)
    for band, setter in (
        (1, renderer.setRedContrastEnhancement),
        (2, renderer.setGreenContrastEnhancement),
        (3, renderer.setBlueContrastEnhancement),
    ):
        stats = provider.bandStatistics(
            band,
            (Qgis.RasterBandStatistic.Min | Qgis.RasterBandStatistic.Max)
            if hasattr(Qgis, "RasterBandStatistic") else QgsRasterBandStats.Min | QgsRasterBandStats.Max,
            layer.extent(),
            250000,
        )
        if np.isfinite((stats.minimumValue, stats.maximumValue)).all() and stats.minimumValue < stats.maximumValue:
            enhancement = QgsContrastEnhancement(provider.dataType(band))
            enhancement.setMinimumValue(stats.minimumValue)
            enhancement.setMaximumValue(stats.maximumValue)
            enhancement.setContrastEnhancementAlgorithm(
                QgsContrastEnhancement.ContrastEnhancementAlgorithm.StretchToMinimumMaximum
            )
            setter(enhancement)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def _rgb_preview_layer(path, title):
    layer = QgsRasterLayer(str(path), title)
    if not layer.isValid():
        raise RuntimeError(f"A composição RGB não pôde ser carregada: {path}")
    apply_rgb_preview_style(layer)
    return layer


def _product_output_path(output_path, product):
    output_path = Path(output_path)
    if not output_path.exists() and not output_path.is_symlink():
        return output_path
    candidate = output_path.with_name(f"{output_path.stem}_{product}{output_path.suffix}")
    counter = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = output_path.with_name(f"{output_path.stem}_{product}_{counter}{output_path.suffix}")
        counter += 1
    return candidate


def _commit_output(staged, output_path, product):
    # Reserva exclusiva evita sobrescrita entre execucoes simultaneas.
    while True:
        output_path = _product_output_path(output_path, product)
        try:
            with output_path.open("xb"):
                pass
            break
        except FileExistsError:
            continue
    try:
        try:
            replace(staged, output_path)
        except OSError as exc:
            if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
                raise
            copyfile(staged, output_path)
            Path(staged).unlink()
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return output_path


def _classification_limits(method, classes, manual_limits):
    if method not in CLASSIFICATION_METHODS and method != "celsius":
        raise RuntimeError("Metodo de classificacao invalido.")
    if not isinstance(classes, int) or isinstance(classes, bool) or not 2 <= classes <= 20:
        raise RuntimeError("Escolha de 2 a 20 classes.")
    if method != "manual":
        return []
    try:
        limits = [float(value.strip().replace(",", ".")) for value in manual_limits.split(";")]
    except (ValueError, AttributeError) as exc:
        raise RuntimeError("Informe limites numericos crescentes separados por ponto e virgula.") from exc
    if not 1 <= len(limits) <= 19 or not np.isfinite(limits).all() or any(a >= b for a, b in zip(limits, limits[1:])):
        raise RuntimeError("Use 1 a 19 limites finitos, estritamente crescentes, sem repeticoes.")
    return limits


def _classify_product(path, product, method="equal", classes=5, manual_limits="", feedback=None):
    """Salva classes discretas em QML; nunca reclassifica os numeros do GeoTIFF."""
    cuts = _classification_limits(method, classes, manual_limits)
    if method == "celsius" and product not in {"lst", "lst_fused"}:
        raise RuntimeError("Intervalos de 1 Celsius sao exclusivos de temperatura.")
    samples = []
    minimum, maximum = float("inf"), float("-inf")
    with gdal.Open(str(path)) as dataset:
        if dataset.RasterCount != 1:
            raise RuntimeError("A classificacao requer um produto continuo de uma banda.")
        band = dataset.GetRasterBand(1)
        nodata = band.GetNoDataValue()
        width, height = dataset.RasterXSize, dataset.RasterYSize
        stride = max(1, ceil(width * height / 10000))
        fallback = None
        for y in range(0, height, 512):
            for x in range(0, width, 512):
                _check_cancel(feedback)
                values = band.ReadAsArray(x, y, min(512, width - x), min(512, height - y))
                valid = np.isfinite(values)
                if nodata is not None:
                    valid &= values != nodata
                if not valid.any():
                    continue
                current = values[valid]
                minimum, maximum = min(minimum, float(current.min())), max(maximum, float(current.max()))
                if fallback is None:
                    fallback = current[::max(1, ceil(current.size / 10000))].copy()
                positions = np.arange(y, y + values.shape[0])[:, None] * width + np.arange(x, x + values.shape[1])
                samples.append(values[valid & (positions % stride == 0)])
    if fallback is None:
        raise RuntimeError("O produto nao possui pixels validos para classificar; revise cobertura e qualidade.")
    sample = np.concatenate(samples)
    if not sample.size:
        sample = fallback
    if method == "celsius":
        cuts = np.arange(floor(minimum) + 1, ceil(maximum), dtype=float).tolist()
    elif method != "manual" and minimum != maximum and (method == "equal" or np.unique(sample).size > 1):
        classifiers = {"equal": QgsClassificationEqualInterval, "quantile": QgsClassificationQuantile,
                       "jenks": QgsClassificationJenks, "stddev": QgsClassificationStandardDeviation}
        classifier = classifiers[method]()
        # ponytail: Jenks O(n^2), amostra deterministica de ate 1000; aumentar apenas com benchmark.
        if method == "jenks" and sample.size > 1000:
            sample = sample[np.linspace(0, sample.size - 1, 1000, dtype=int)]
        ranges = (classifier.classes(minimum, maximum, classes) if method == "equal"
                  else classifier.classes(sample.astype(float).tolist(), classes))
        cuts = sorted({item.upperBound() for item in ranges[:-1] if minimum < item.upperBound() < maximum})
    bounds = [*cuts, float("inf")]
    palettes = {"ndvi": ("#a50026", "#006837"), "savi": ("#8c510a", "#01665e"),
                "evi2": ("#a50026", "#006837"),
                "vari": ("#a50026", "#006837"),
                "ndwi": ("#f1d6a8", "#08519c"), "ndbi": ("#2c7bb6", "#d7191c"),
                "lst": ("#2166ac", "#b2182b"), "lst_fused": ("#2166ac", "#b2182b")}
    from qgis.core import QgsGradientColorRamp, QgsGradientStop
    ramp = QgsGradientColorRamp(*(QColor(color) for color in palettes[product]))
    if product in {"ndvi", "savi", "evi2", "vari"}:
        if product == "savi":
            ramp = QgsGradientColorRamp(QColor("#a50026"), QColor("#006837"))
        ramp.setStops([QgsGradientStop(0.5, QColor("#ffffbf"))])
    color_shader = QgsColorRampShader(minimum, maximum)
    color_shader.setSourceColorRamp(ramp.clone())
    color_shader.setColorRampType(QgsColorRampShader.Discrete)
    color_shader.setClip(False)
    items = []
    unit = " Celsius" if product in {"lst", "lst_fused"} else ""
    levels = ("muito baixo", "baixo", "medio", "alto", "muito alto")
    title = "LST estimada" if product == "lst_fused" else product.upper()
    number = lambda value: f"{value:.6g}".replace(".", ",")
    for index, upper in enumerate(bounds):
        label = (f"<= {number(upper)}{unit}" if index == 0 else
                 f"> {number(bounds[index - 1])}{unit}" if np.isinf(upper) else
                 f"> {number(bounds[index - 1])} a <= {number(upper)}{unit}")
        if len(bounds) == 1:
            label = (f"{title} constante: {number(minimum)}{unit}" if minimum == maximum else
                     f"{title}: {number(minimum)} a {number(maximum)}{unit}")
        else:
            rank = levels[index] if len(bounds) == 5 else f"classe {index + 1}/{len(bounds)}"
            label = f"{title} ({label})" if method == "celsius" else f"{title} {rank} (relativo; {label})"
        items.append(QgsColorRampShader.ColorRampItem(upper, ramp.color(index / max(1, len(bounds) - 1)), label))
    color_shader.setColorRampItemList(items)
    legend = QgsColorRampLegendNodeSettings()
    legend.setUseContinuousLegend(False)
    color_shader.setLegendSettings(legend)
    shader = QgsRasterShader(minimum, maximum)
    shader.setRasterShaderFunction(color_shader)
    # Camada isolada, criada e destruida nesta thread; nao toca no projeto ou na interface.
    layer = QgsRasterLayer(str(path), product.upper())
    try:
        if not layer.isValid():
            raise RuntimeError("O produto nao pode ser aberto para salvar a classificacao.")
        layer.setRenderer(QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader))
        layer.renderer().setClassificationMin(minimum)
        layer.renderer().setClassificationMax(maximum)
        layer.setScaleBasedVisibility(False)
        classification_details = json.dumps({
            "method": method, "requested_classes": classes, "actual_classes": len(bounds), "limits": cuts,
            "sample_size": int(sample.size), "minimum": minimum, "maximum": maximum,
            "interpretation": "Classes numericas; nao sao classes validadas de cobertura da terra.",
        }, ensure_ascii=False)
        layer.setCustomProperty("satelite/classification", classification_details)
        message, saved = layer.saveNamedStyle(str(Path(path).with_suffix(".qml")))
    finally:
        del layer
    if not saved or not Path(path).with_suffix(".qml").is_file():
        raise RuntimeError(f"Nao foi possivel salvar o estilo classificado: {message}")
    if feedback is not None:
        feedback.pushInfo(f"Classificacao: {CLASSIFICATION_METHODS.get(method, 'Intervalos de 1 Celsius')}, {len(bounds)} classes; valores originais preservados.")


def _write_processing_output(source_path, output_path, spectral_index=False, product="mosaic", feedback=None,
                             classification="equal", class_count=5, manual_limits=""):
    if output_path is None or source_path is None:
        return None
    if Path(source_path).resolve() == Path(output_path).resolve():
        raise RuntimeError("A saida nao pode substituir a propria origem.")
    classify = spectral_index and product in {"ndvi", "ndwi", "ndbi", "savi", "evi2", "vari", "lst", "lst_fused"}
    if classify:
        _classification_limits(classification, class_count, manual_limits)
    output_path = _product_output_path(output_path, product)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _check_cancel(feedback)
    with TemporaryDirectory(prefix="qgis_output_", dir=output_path.parent) as temporary:
        staged = Path(temporary) / output_path.name
        dataset = None
        try:
            dataset = gdal.Translate(
                str(staged), str(source_path),
                options=gdal.TranslateOptions(
                    format="GTiff",
                    creationOptions=["TILED=YES", "COMPRESS=DEFLATE", f"PREDICTOR={3 if spectral_index else 2}", "BIGTIFF=IF_SAFER"],
                    callback=_gdal_progress(feedback),
                ),
            )
            if dataset is None:
                raise RuntimeError(f"Nao foi possivel gravar o raster: {output_path}")
            _build_overviews(dataset, "NEAREST" if product == "mask" else "AVERAGE", feedback)
        finally:
            if dataset is not None:
                dataset.Close()
        _check_cancel(feedback)
        if classify:
            _classify_product(staged, product, classification, class_count, manual_limits, feedback)
            if output_path.with_suffix(".qml").exists():
                raise RuntimeError("Ja existe um estilo QML para esse destino. Escolha outro nome para preservar o estilo.")
        output_path = _commit_output(staged, output_path, product)
        if classify:
            style_path = _commit_output(staged.with_suffix(".qml"), output_path.with_suffix(".qml"), "style")
            if style_path != output_path.with_suffix(".qml"):
                raise RuntimeError(f"Conflito de estilo; raster preservado em {output_path}, estilo em {style_path}.")
    return output_path


def _quality_key(source):
    return MULTIBAND_SOURCES[source].get("quality", "scl" if source == "l2a" else "qa_pixel")


def _asset_nodata(source, key):
    if source.startswith("hls_"):
        return 255 if key == "fmask" else -9999
    return 65535 if key == "qa_pixel" else 0


def _quality_invalid(source, values):
    if source == "l2a":
        return ~np.isin(values, (4, 5, 6))
    if source.startswith("hls_"):
        return (values == 255) | ((values & 0b00011110) != 0) | ((values & 0b11000000) == 0b11000000)
    return (values & 0b00111111) != 0




@gdal.config_options({**NETWORK_OPTIONS, "GDAL_NUM_THREADS": "1"})
def _warp_remote_with_retry(destination, source, options, label, feedback=None, refresh_source=None):
    """Repete uma leitura COG após limpar blocos HTTP possivelmente corrompidos."""
    last_error = None
    for attempt in range(2):
        _check_cancel(feedback)
        if destination is not None:
            destination.unlink(missing_ok=True)
        try:
            fallback = {"GDAL_HTTP_VERSION": "1.1", "VSI_CACHE": "FALSE", "CPL_VSIL_CURL_NON_CACHED": source} if attempt else {}
            with gdal.config_options(fallback):
                dataset = gdal.Warp(str(destination) if destination is not None else "", source, options=options)
            if dataset is not None:
                return dataset
            last_error = RuntimeError("GDAL retornou saída vazia")
        except RuntimeError as exc:
            last_error = exc
        _check_cancel(feedback)
        if refresh_source is not None and "403" in str(last_error):
            source = refresh_source()
        elif isinstance(source, str) and source.startswith("/vsicurl/"):
            gdal.VSICurlPartialClearCache(source)
        if attempt == 0 and feedback is not None:
            feedback.pushWarning(f"Falha temporária ao ler {label}; limpando cache e tentando novamente.")
    if feedback is not None:
        detail = "HTTP 404" if "404" in str(last_error) else "erro de acesso/leitura COG"
        feedback.pushWarning(f"{label} ignorada apos duas tentativas: {detail}")
    if destination is not None:
        destination.unlink(missing_ok=True)
    return None


def _warp_asset(item, key, bounds, crs, resolution, temporary, token_state, feedback):
    asset = item.get("assets", {}).get(key)
    if not asset:
        return None
    path = temporary / f"{quote(str(item['id']), safe='')}_{key}_{resolution}m.tif"
    if path.is_file():
        return path
    original_token = token_state.get("token")
    nodata = _asset_nodata(token_state.get("source", "l2a"), key)

    def signed_source():
        return _asset_path(asset["href"], token_state.get("token"))

    def renew_source():
        with token_state.setdefault("lock", threading.Lock()):
            if token_state.get("token") == original_token:
                token_state["token"] = _planetary_computer_token(token_state["collection"])
        return signed_source()

    dataset = _warp_remote_with_retry(
        path, signed_source(),
        gdal.WarpOptions(
            format="GTiff", dstSRS=crs, outputBounds=bounds,
            xRes=resolution, yRes=resolution, resampleAlg="near", overviewLevel="NONE",
            srcNodata=nodata, dstNodata=nodata, errorThreshold=0.0,
            warpMemoryLimit=32,
            creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"],
            callback=_gdal_progress(feedback),
        ),
        f"{key} da cena {item['id']}", feedback,
        renew_source if token_state.get("token") else None,
    )
    if dataset is None:
        return None
    dataset.Close()
    return path


def _prepare_observations(scenes, source, bounds, crs, resolution, temporary, token_state, feedback,
                          download_workers=ANALYTIC_DOWNLOAD_WORKERS, required_keys=None):
    """Seleciona uma observacao comum para todas as bandas opticas em cada pixel."""
    keys = ("red", "green", "blue", "nir" if source == "l2a" else "nir08", "swir16", "swir22")
    if required_keys is not None:
        keys = tuple(required_keys)
    quality_key = _quality_key(source)
    columns = round((bounds[2] - bounds[0]) / resolution)
    rows = round((bounds[3] - bounds[1]) / resolution)
    choice_path = temporary / "CENA_POR_PIXEL.tif"
    observations = []
    calibration = None
    valid_count = 0
    if not isinstance(download_workers, int) or isinstance(download_workers, bool) or not 1 <= download_workers <= 4:
        raise RuntimeError("Use 1 a 4 downloads analiticos simultaneos.")
    with ThreadPoolExecutor(max_workers=download_workers) as executor, gdal.GetDriverByName("GTiff").Create(
        str(choice_path), columns, rows, 1, gdal.GDT_UInt16,
        options=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"],
    ) as choice:
        choice.SetProjection(crs)
        choice.SetGeoTransform((bounds[0], resolution, 0, bounds[3], 0, -resolution))
        choice.GetRasterBand(1).SetNoDataValue(0)
        choice.GetRasterBand(1).Fill(0)
        for position, item in enumerate(scenes, 1):
            _check_cancel(feedback)
            if any(key not in item.get("assets", {}) for key in (*keys, quality_key)):
                continue
            current = {key: _band_calibration(source, key, item["assets"][key]) for key in keys}
            if calibration is not None and current != calibration:
                if feedback is not None:
                    feedback.pushWarning(f"Cena {item['id']} ignorada: calibracao diferente da cena principal.")
                continue
            quality_path = _warp_asset(item, quality_key, bounds, crs, resolution, temporary, token_state, feedback)
            if quality_path is None:
                continue
            possible_gain = False
            with gdal.Open(str(quality_path)) as quality:
                for row in range(0, rows, 512):
                    _check_cancel(feedback)
                    height = min(512, rows - row)
                    for column in range(0, columns, 512):
                        width = min(512, columns - column)
                        empty = choice.ReadAsArray(column, row, width, height) == 0
                        possible_gain |= bool(np.any(empty & ~_quality_invalid(source, quality.ReadAsArray(column, row, width, height))))
                        if possible_gain:
                            break
                    if possible_gain:
                        break
            if not possible_gain:
                continue
            paths = dict(zip(keys, executor.map(
                lambda key: _warp_asset(item, key, bounds, crs, resolution, temporary, token_state, feedback), keys,
            )))
            paths[quality_key] = quality_path
            if any(path is None for path in paths.values()):
                continue
            added = 0
            with ExitStack() as stack:
                datasets = {key: stack.enter_context(gdal.Open(str(path))) for key, path in paths.items()}
                for row in range(0, rows, 512):
                    _check_cancel(feedback)
                    height = min(512, rows - row)
                    for column in range(0, columns, 512):
                        width = min(512, columns - column)
                        selected = choice.GetRasterBand(1).ReadAsArray(column, row, width, height)
                        quality = datasets[quality_key].ReadAsArray(column, row, width, height)
                        valid = (selected == 0) & ~_quality_invalid(source, quality)
                        for key in keys:
                            valid &= datasets[key].ReadAsArray(column, row, width, height) != _asset_nodata(source, key)
                        added += int(np.count_nonzero(valid))
                        selected[valid] = position
                        choice.GetRasterBand(1).WriteArray(selected, column, row)
            if added:
                calibration = current
                valid_count += added
                observations.append({"scene": item, "index": position})
            if feedback is not None:
                feedback.setProgressText(f"Observacao {position}/{len(scenes)}: {valid_count / (columns * rows):.1%} de cobertura real")
            if valid_count == columns * rows:
                break
        choice.FlushCache()
    if not observations:
        raise RuntimeError("Nenhuma observacao optica valida e coerente foi obtida; ajuste o ano, as nuvens ou a extensao.")
    coverage = valid_count / (columns * rows)
    if feedback is not None:
        feedback.pushInfo(f"Cobertura optica real: {coverage:.1%}; {len(observations)} cena(s).")
        if coverage < 0.98:
            feedback.pushWarning("Cobertura incompleta: lacunas reais permanecem como NoData. Para Landsat 7 recente, prefira Landsat 8-9.")
    return observations, choice_path, coverage


def _compose_observation_band(observations, choice_path, source, key, resolution, bounds, crs,
                              temporary, token_state, feedback):
    """A mesma cena prevalece em todas as bandas; ausencias nao usam outra data."""
    selection_path = temporary / f"selection_{resolution}m.tif"
    if not selection_path.exists():
        with gdal.Warp(
            str(selection_path), str(choice_path),
            options=gdal.WarpOptions(
                format="GTiff", dstSRS=crs, outputBounds=bounds,
                xRes=resolution, yRes=resolution, resampleAlg="near", overviewLevel="NONE",
                srcNodata=0, dstNodata=0,
                creationOptions=["TILED=YES", "COMPRESS=DEFLATE"],
                callback=_gdal_progress(feedback),
            ),
        ):
            pass
    output_path = temporary / f"{key}_mosaic.tif"
    output = None
    calibration = None
    valid_count = 0
    try:
        with gdal.Open(str(selection_path)) as selection:
            for observation in observations:
                _check_cancel(feedback)
                item = observation["scene"]
                asset = item.get("assets", {}).get(key)
                if not asset:
                    continue
                current = _band_calibration(source, key, asset)
                if calibration is not None and current != calibration:
                    if feedback is not None:
                        feedback.pushWarning(f"{key}: calibracao diferente em {item['id']}; pixels permanecem NoData.")
                    continue
                path = _warp_asset(item, key, bounds, crs, resolution, temporary, token_state, feedback)
                if path is None:
                    continue
                with gdal.Open(str(path)) as scene:
                    if output is None:
                        output = gdal.GetDriverByName("GTiff").Create(
                            str(output_path), selection.RasterXSize, selection.RasterYSize, 1, scene.GetRasterBand(1).DataType,
                            options=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"],
                        )
                        output.SetProjection(crs)
                        output.SetGeoTransform(selection.GetGeoTransform())
                        output.GetRasterBand(1).SetNoDataValue(_asset_nodata(source, key))
                        output.GetRasterBand(1).Fill(_asset_nodata(source, key))
                        calibration = current
                    for row in range(0, scene.RasterYSize, 512):
                        _check_cancel(feedback)
                        height = min(512, scene.RasterYSize - row)
                        for column in range(0, scene.RasterXSize, 512):
                            width = min(512, scene.RasterXSize - column)
                            selected = selection.ReadAsArray(column, row, width, height) == observation["index"]
                            values = scene.ReadAsArray(column, row, width, height)
                            existing = output.GetRasterBand(1).ReadAsArray(column, row, width, height)
                            existing[selected] = values[selected]
                            valid_count += int(np.count_nonzero(values[selected] != _asset_nodata(source, key)))
                            output.GetRasterBand(1).WriteArray(existing, column, row)
            if output is None:
                return None
            if calibration[0] is not None:
                output.GetRasterBand(1).SetScale(calibration[0])
                output.GetRasterBand(1).SetOffset(calibration[1] or 0.0)
            output.SetMetadataItem("BAND_VALID_FRACTION", str(valid_count / (output.RasterXSize * output.RasterYSize)))
            output.FlushCache()
    finally:
        if output is not None:
            output.Close()
    return output_path, calibration


def _saved_bands(source, directory):
    directory = Path(directory)
    manifest_path = directory / MANIFEST_NAME
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source") != source or not manifest.get("coherent_observations"):
            raise RuntimeError("O manifesto nao corresponde a fonte/coerencia selecionada.")
        bands = {}
        for key, value in manifest["bands"].items():
            path = (directory / value["file"]).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():
                raise RuntimeError("Arquivo de banda ausente ou fora da pasta do manifesto.")
            bands[key] = (int(value["resolution"]), path)
        return bands
    return {
        asset_key: (resolution, path)
        for asset_key, band_name, resolution, _ in MULTIBAND_SOURCES[source]["bands"]
        if (path := directory / f"{band_name}_{resolution}m.tif").is_file()
    }


def _raster_bounds(path):
    dataset = gdal.Open(str(path))
    if dataset is None:
        raise RuntimeError(f"Não foi possível abrir a banda baixada: {path}")
    geotransform = dataset.GetGeoTransform()
    if geotransform is None:
        raise RuntimeError(f"A banda baixada não possui georreferenciamento: {path}")
    if geotransform[2] or geotransform[4] or geotransform[1] <= 0 or geotransform[5] >= 0:
        dataset.Close()
        raise RuntimeError("Grade rotacionada/invertida nao suportada para o produto; normalize a grade antes.")
    xmin, ymax = geotransform[0], geotransform[3]
    xmax = xmin + dataset.RasterXSize * geotransform[1]
    ymin = ymax + dataset.RasterYSize * geotransform[5]
    dataset = None
    return xmin, ymin, xmax, ymax


def _composition_assets(source, composition):
    try:
        keys = RGB_COMPOSITIONS[composition]
    except KeyError as exc:
        raise RuntimeError("Composição RGB inválida.") from exc
    return tuple("nir" if key == "nir" and source == "l2a" else "nir08" if key == "nir" else key for key in keys)


def _create_fused_lst(source, directory, bands, temporary, fill_gaps=False, min_r2=0.2, feedback=None):
    """Fusao local por cena; nenhuma banda de origem e alterada."""
    manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    scene_path = (directory / manifest.get("scene_index", "")).resolve()
    scenes = manifest.get("scenes", [])
    if not scene_path.is_relative_to(directory.resolve()) or not scene_path.is_file() or not scenes:
        raise RuntimeError("Fusao exige manifesto com cenas e mapa CENA_POR_PIXEL valido.")
    thermal = "lwir11" if source == "landsat89" else "lwir"
    required = ("red", "nir08", thermal, "qa_pixel")
    if source == "l2a" or any(key not in bands for key in required):
        raise RuntimeError("Fusao exige Landsat com Red, NIR, ST e QA_PIXEL da mesma observacao.")
    with ExitStack() as stack:
        datasets = [stack.enter_context(gdal.Open(str(bands[key][1]))) for key in required]
        reference = datasets[0]
        spatial_ref = reference.GetSpatialRef()
        if spatial_ref is None or not spatial_ref.IsProjected() or abs(spatial_ref.GetLinearUnits() - 1) > 1e-6:
            raise RuntimeError("Fusao exige SRC projetado metrico valido.")
        transform, projection = reference.GetGeoTransform(), reference.GetProjection()
        size = reference.RasterXSize, reference.RasterYSize
        # ponytail: fusao em memoria, ate 10 milhoes de pixels; dividir area maior ou implementar janelas.
        if size[0] * size[1] > 10_000_000:
            raise RuntimeError("Fusao limitada a 10 milhoes de pixels; divida a area em recortes menores.")
        scene_dataset = stack.enter_context(gdal.Open(str(scene_path)))
        for dataset in (*datasets, scene_dataset):
            if ((dataset.RasterXSize, dataset.RasterYSize) != size or dataset.GetGeoTransform() != transform
                    or not dataset.GetSpatialRef().IsSame(reference.GetSpatialRef())):
                raise RuntimeError("Grades das bandas/cenas diferentes; baixe novamente antes de fundir.")
        if abs(transform[1] - 30) > 1e-6 or abs(transform[5] + 30) > 1e-6 or transform[2] or transform[4]:
            raise RuntimeError("A fusao local exige grade Landsat de 30 m, sem rotacao.")
        choices = scene_dataset.ReadAsArray()
        water = (datasets[-1].ReadAsArray() & 128) != 0
    bounds = _raster_bounds(bands["red"][1])
    _, _, ndvi_path, _ = _create_spectral_index(source, "ndvi", bands, bounds, temporary, temporary, feedback)
    _, _, original_path, _ = _create_spectral_index(source, "lst", bands, bounds, temporary, temporary, feedback)
    with gdal.Open(str(ndvi_path)) as dataset:
        ndvi = dataset.ReadAsArray()
        ndvi[(ndvi == -9999) | water] = np.nan
    with gdal.Open(str(original_path)) as dataset:
        original = dataset.ReadAsArray()
    fused = original.copy()
    flags = (original != -9999).astype(np.uint8)
    factor = 2 if source == "landsat7" else 4
    reports = []
    indexes = set()
    for scene in scenes:
        _check_cancel(feedback)
        index = scene.get("index")
        if not isinstance(index, int) or index <= 0 or index in indexes or not scene.get("date") or not scene.get("id"):
            raise RuntimeError("Manifesto com identificadores/datas de cena invalidos.")
        indexes.add(index)
        selected = choices == index
        if not selected.any():
            continue
        values, quality, report = _tsharp_scene(ndvi, original, selected, factor, fill_gaps, min_r2, feedback)
        fused[selected], flags[selected] = values[selected], quality[selected]
        report.update(scene=scene["id"], date=scene["date"])
        reports.append(report)
        if feedback is not None:
            if report["status"] == "fitted":
                feedback.pushInfo(f"Fusao {scene['id']}: R2 holdout={report['validation_r2']:.3f}, RMSE={report['validation_rmse_c']:.2f} C; validacao interna, nao precisao a 30 m.")
            else:
                feedback.pushWarning(f"Fusao {scene['id']} recusada: {report['reason']}")
    if np.any((choices != 0) & ~np.isin(choices, list(indexes))):
        raise RuntimeError("Mapa de cenas contem observacoes ausentes do manifesto.")
    if not np.any((flags == 2) | (flags == 3)):
        raise RuntimeError("Nenhuma cena possui suporte/validacao para fusao; use LST original ou uma area com mais variacao.")
    report = {"method": "TsHARP adaptado por cena; base 1-(1-NDVI)^0.625; conservacao de Kelvin^4",
              "reference": METHOD_REFERENCES["lst_fused"], "version": VERSION,
              "output_grid_m": 30, "aggregation_support_m": factor * 30,
              "fill_gaps": bool(fill_gaps), "minimum_validation_r2": min_r2,
              "validation_warning": "Holdout espacial na grade agregada; nao comprova precisao termica a 30 m. Extrapolacao de escala e possivel.",
              "valid_fraction": float(np.count_nonzero(fused != -9999) / fused.size),
              "mask_codes": {"0": "NoData", "1": "LST original mantida", "2": "LST estimada com suporte termico", "3": "Lacuna estimada; sem temperatura observada"},
              "scenes": reports}
    output_path, mask_path = temporary / "LST_Fusionada.tif", temporary / "LST_Estimativa_Mask.tif"
    for path, array, dtype, nodata in ((output_path, fused, gdal.GDT_Float32, -9999), (mask_path, flags, gdal.GDT_Byte, 0)):
        with gdal.GetDriverByName("GTiff").Create(str(path), size[0], size[1], 1, dtype,
                options=["TILED=YES", "COMPRESS=DEFLATE", f"PREDICTOR={3 if dtype == gdal.GDT_Float32 else 2}"]) as dataset:
            dataset.SetGeoTransform(transform)
            dataset.SetProjection(projection)
            dataset.GetRasterBand(1).SetNoDataValue(nodata)
            dataset.GetRasterBand(1).WriteArray(array)
            dataset.SetMetadata({"PRODUCT_TYPE": "ESTIMATED_LST" if path == output_path else "ESTIMATION_PROVENANCE",
                                 "METHOD_VERSION": VERSION, "METHOD_REFERENCE": METHOD_REFERENCES["lst_fused"],
                                 "INDEX_UNIT": "C" if path == output_path else "code", "VALID_FRACTION": str(report["valid_fraction"]),
                                 "AGGREGATION_SUPPORT_M": str(factor * 30), "OUTPUT_GRID_M": "30"})
    return original_path, output_path, mask_path, report


def _prepare_product_grid(path, temporary, product, mask=None, target_crs=None, feedback=None):
    if not mask and not target_crs:
        return path
    with gdal.Open(str(path)) as source:
        projection = target_crs or source.GetProjection()
        crs = osr.SpatialReference()
        if crs.SetFromUserInput(projection) != 0 or not crs.IsProjected() or abs(crs.GetLinearUnits() - 1) > 1e-6:
            raise RuntimeError("Escolha um SRC projetado em metros para preservar a resolucao do produto.")
        transform = source.GetGeoTransform()
        nodata = source.GetRasterBand(1).GetNoDataValue()
        nodata = nodata if nodata is not None else (0 if source.RasterCount > 1 or product == "mask" else -9999)
    destination = Path(temporary) / (Path(path).stem + "_grade.tif")
    options = dict(dstSRS=projection, xRes=abs(transform[1]), yRes=abs(transform[5]),
                   srcNodata=nodata, dstNodata=nodata, resampleAlg="near", overviewLevel="NONE",
                   creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"],
                   callback=_gdal_progress(feedback))
    if mask:
        vector = ogr.Open(str(mask))
        if vector is None or vector.GetLayer(0).GetFeatureCount() == 0 or vector.GetLayer(0).GetSpatialRef() is None:
            raise RuntimeError("Limite vazio, ilegivel ou sem SRC.")
        options.update(cutlineDSName=str(mask), cropToCutline=True)
        vector = None
    with gdal.Warp(str(destination), str(path), **options) as ds:
        if mask:
            polygon = gdal.GetDriverByName("MEM").Create("", ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Byte)
            polygon.SetGeoTransform(ds.GetGeoTransform())
            polygon.SetProjection(ds.GetProjection())
            gdal.Rasterize(polygon, str(mask), options=gdal.RasterizeOptions(burnValues=[1]))
            inside_pixels, valid_pixels = 0, 0
            for row in range(0, ds.RasterYSize, 512):
                _check_cancel(feedback)
                height = min(512, ds.RasterYSize - row)
                outside = polygon.ReadAsArray(0, row, ds.RasterXSize, height) == 0
                inside_pixels += int(np.count_nonzero(~outside))
                valid = ~outside
                for index in range(1, ds.RasterCount + 1):
                    band = ds.GetRasterBand(index)
                    values = band.ReadAsArray(0, row, ds.RasterXSize, height)
                    values[outside] = nodata
                    valid &= np.isfinite(values) & (values != nodata)
                    band.WriteArray(values, 0, row)
                valid_pixels += int(np.count_nonzero(valid))
            if not inside_pixels:
                raise RuntimeError("O limite nao possui pixels na grade de saida.")
            ds.SetMetadataItem("CLIP_VALID_FRACTION", str(valid_pixels / inside_pixels))
            ds.SetMetadataItem("CLIP_PIXELS", str(inside_pixels))
        ds.SetMetadataItem("OUTPUT_RESOLUTION_M", str(abs(ds.GetGeoTransform()[1])))
    return destination


def _product_report(path, product, directory, feedback=None, classification_method=None):
    layer = QgsRasterLayer(str(path), product)
    style_path = Path(path).with_suffix(".qml")
    message, loaded = layer.loadNamedStyle(str(style_path))
    if not loaded:
        nearby = ", ".join(item.name for item in style_path.parent.glob("*.qml"))
        raise RuntimeError(f"Nao foi possivel carregar o estilo {style_path}: {message}; encontrados: {nearby}")
    items = layer.renderer().shader().rasterShaderFunction().colorRampItemList()
    classification_text = layer.customProperty("satelite/classification")
    if not classification_text:
        with gdal.Open(str(path)) as dataset:
            classification_text = dataset.GetMetadataItem("CLASSIFICATION_JSON")
    classification = json.loads(classification_text) if classification_text else {
        "method": classification_method or "qml", "requested_classes": len(items),
        "actual_classes": len(items), "limits": [item.value for item in items[:-1] if np.isfinite(item.value)],
        "sample_size": None, "minimum": items[0].value if items else None,
        "maximum": items[-2].value if len(items) > 1 else None,
        "interpretation": "Classes numericas; nao sao classes validadas de cobertura da terra.",
    }
    counts = np.zeros(len(items), dtype=np.int64)
    with gdal.Open(str(path)) as ds:
        band = ds.GetRasterBand(1)
        gt = ds.GetGeoTransform()
        metadata = ds.GetMetadata()
        crs = osr.SpatialReference(wkt=ds.GetProjection())
        area = abs(gt[1]*gt[5]-gt[2]*gt[4]) * crs.GetLinearUnits()**2 / 1e6 if crs.IsProjected() else None
        above60 = 0
        for row in range(0, ds.RasterYSize, 512):
            _check_cancel(feedback)
            values = band.ReadAsArray(0, row, ds.RasterXSize, min(512, ds.RasterYSize-row))
            valid = np.isfinite(values)
            if band.GetNoDataValue() is not None:
                valid &= values != band.GetNoDataValue()
            values = values[valid]
            counts += np.bincount(np.searchsorted(classification['limits'], values, side='left'), minlength=len(items))
            above60 += int(np.count_nonzero(values > 60))
    manifest = Path(directory) / MANIFEST_NAME
    provenance = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    report = {"product": product, "version": VERSION, "classification": classification,
              "scenes": provenance.get("scenes", []), "download_profile": provenance.get("download_profile", "complete"),
              "valid_pixels": int(counts.sum()), "classes": [
                  {"label": item.label, "pixels": int(count), "area_km2": float(count*area) if area is not None else None}
                  for item,count in zip(items,counts)],
              "output_resolution_m": abs(gt[1])*crs.GetLinearUnits() if crs.IsProjected() else None,
              "coverage_within_boundary": float(metadata['CLIP_VALID_FRACTION']) if 'CLIP_VALID_FRACTION' in metadata else None,
              "reference": METHOD_REFERENCES[product],
              "limitations": "Classes numericas, nao uso do solo validado. Datas diferentes nao isolam efeito da resolucao. QA e mascara nao substituem validacao independente."}
    if product in {"lst", "lst_fused"}:
        report.update(above_60_celsius=above60, extreme_note="60 Celsius e marcador exploratorio; nao descartar automaticamente.",
                      thermal_note="Nao valida ST_QA, distancia a nuvens ou saturacao; temperatura de superficie, nao do ar.")
        if classification['method'] == 'celsius':
            report['classification_reference'] = 'submissao-2752-arquivo-11261-1.pdf, p. 3 (p. impressa 96).'
    if product == 'savi':
        report['savi_l'] = float(metadata['SAVI_L']) if 'SAVI_L' in metadata else None
        report['soil_reference'] = 'Aplicacaoindices.pdf, p. 4 e 6; L=0,5 e padrao, nao limiar universal.'
    with TemporaryDirectory(prefix="qgis_report_", dir=Path(path).parent) as temporary:
        staged = Path(temporary) / "report.json"
        staged.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        return str(_commit_output(staged, Path(path).with_suffix(".json"), "report"))


ECOSTRESS_CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
ECOSTRESS_DOWNLOAD_HOST = "data.lpdaac.earthdatacloud.nasa.gov"


class _EarthdataRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != "https":
            raise RuntimeError("Earthdata redirecionou para uma conexao sem TLS.")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected and urlparse(newurl).hostname not in {ECOSTRESS_DOWNLOAD_HOST, "urs.earthdata.nasa.gov"}:
            redirected.remove_header("Authorization")
        return redirected


def search_ecostress(bounds, start, end, output_folder, granule_id="", token=None, feedback=None):
    """Consulta publica CMR; somente um granulo explicitamente escolhido e baixado."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise RuntimeError("Data inicial deve ser anterior ou igual a final.")
    if len(bounds) != 4 or not np.isfinite(bounds).all() or not (-180 <= bounds[0] < bounds[2] <= 180
            and -90 <= bounds[1] < bounds[3] <= 90):
        raise RuntimeError("Extensao ECOSTRESS invalida em WGS84.")
    destination = Path(output_folder)
    destination.mkdir(parents=True, exist_ok=True)
    scenes = []
    for page in range(1, 11):
        _check_cancel(feedback)
        query = urlencode({"short_name": "ECO_L2T_LSTE", "version": "003", "page_size": 100,
            "page_num": page, "bounding_box": ",".join(map(str, bounds)),
            "temporal": f"{start}T00:00:00Z,{end}T23:59:59Z", "sort_key": "-start_date"})
        try:
            with urlopen(Request(ECOSTRESS_CMR_URL + "?" + query), timeout=60) as response:
                entries = json.load(response)["feed"]["entry"]
        except (OSError, ValueError, KeyError):
            raise RuntimeError("Falha ao consultar CMR ECOSTRESS. Confira conexao e periodo.") from None
        for entry in entries:
            assets = {}
            for link in entry.get("links", []):
                href = link.get("href", "")
                parsed = urlparse(href)
                if parsed.scheme == "https" and parsed.hostname == ECOSTRESS_DOWNLOAD_HOST and not parsed.query:
                    for key in ("LST", "QC", "cloud"):
                        if parsed.path.endswith("_" + key + ".tif"):
                            assets[key] = href
            if len(assets) == 3:
                scenes.append({"id": entry["title"], "date": entry["time_start"], "assets": assets})
        if len(entries) < 100:
            break
    else:
        raise RuntimeError("Mais de 1000 granulos ECOSTRESS; reduza o periodo/extensao.")
    report = {"product": "ECO_L2T_LSTE.003", "version": VERSION, "bounds_wgs84": list(bounds),
        "dates": [start, end], "source": ECOSTRESS_CMR_URL, "reference": METHOD_REFERENCES["ecostress"],
        "scenes": scenes, "note": "Tiles podem ter cobertura parcial. Nao combinar horarios diferentes como uma cena unica."}
    catalog = _product_output_path(destination / "ECOSTRESS_CATALOGO.json", "catalogo")
    catalog.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if feedback is not None:
        feedback.pushInfo(f"ECOSTRESS: {len(scenes)} granulos; catalogo: {catalog}")
    if not granule_id:
        return str(catalog), None
    selected = next((scene for scene in scenes if scene["id"] == granule_id.strip()), None)
    if selected is None:
        raise RuntimeError("Granulo nao consta nesta consulta. Consulte o ID exato no catalogo JSON.")
    if Path(selected["id"]).name != selected["id"] or not selected["id"].startswith("ECOv003_L2T_LSTE_"):
        raise RuntimeError("Identificador ECOSTRESS inseguro ou inesperado.")
    if not token or any(character.isspace() for character in token):
        raise RuntimeError("Configure um token Earthdata valido na autenticacao Basica do QGIS (campo Senha).")
    opener = build_opener(_EarthdataRedirect())
    with TemporaryDirectory(prefix="ecostress_download_", dir=destination) as temporary:
        staged = Path(temporary)
        for key, href in selected["assets"].items():
            _check_cancel(feedback)
            path = staged / Path(urlparse(href).path).name
            try:
                with opener.open(Request(href, headers={"Authorization": "Bearer " + token}), timeout=60) as response, path.open("wb") as target:
                    total = 0
                    while True:
                        _check_cancel(feedback)
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > 256 * 1024**2:
                            raise RuntimeError("Arquivo ECOSTRESS excede 256 MB; confira o produto L2T.")
                        target.write(block)
                with gdal.Open(str(path)) as dataset:
                    if dataset is None or dataset.RasterCount != 1 or not dataset.GetProjection():
                        raise RuntimeError("Resposta Earthdata nao e um GeoTIFF L2T georreferenciado.")
            except HTTPError as exc:
                raise RuntimeError(f"Earthdata HTTP {exc.code}: confira token, autorizacao LP DAAC e validade.") from None
            except OSError:
                raise RuntimeError("Falha de rede/disco no download Earthdata; nenhum arquivo parcial foi publicado.") from None
        (staged / "ECOSTRESS_ORIGEM.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")
        folder = _product_output_path(destination / selected["id"], "granulo")
        if not folder.resolve().is_relative_to(destination.resolve()):
            raise RuntimeError("Destino do granulo fora da pasta de saida.")
        replace(staged, folder)
    return str(catalog), str(folder)


def generate_ecostress_lst(lst_path, qc_path, cloud_path, output, feedback=None, mask=None,
                           classification="equal", class_count=5, manual_limits="", thermal_1c=False):
    """L2T Float32 Kelvin, mesma grade e granulo; QC e cloud sao obrigatorios."""
    method = "celsius" if thermal_1c else classification
    _classification_limits(method, class_count, manual_limits)
    inputs = {"LST": Path(lst_path), "QC": Path(qc_path), "cloud": Path(cloud_path)}
    granules = set()
    for key, path in inputs.items():
        suffix = "_" + key
        if not path.stem.endswith(suffix) or not path.name.startswith(("ECOv003_L2T_LSTE_", "ECOv002_L2T_LSTE_")):
            raise RuntimeError("Use os GeoTIFFs L2T LSTE originais (LST, QC e cloud), sem renomear.")
        granules.add(path.stem.removesuffix(suffix))
    if len(granules) != 1:
        raise RuntimeError("LST, QC e cloud devem ser do mesmo granulo/data.")
    with TemporaryDirectory(prefix="ecostress_lst_") as temporary, ExitStack() as stack:
        datasets = {key: stack.enter_context(gdal.Open(str(path))) for key, path in inputs.items()}
        lst = datasets["LST"]
        if lst is None or not lst.GetProjection() or lst.RasterCount != 1:
            raise RuntimeError("LST invalida ou sem georreferenciamento.")
        crs = lst.GetSpatialRef()
        transform = lst.GetGeoTransform()
        if (not crs.IsProjected() or abs(crs.GetLinearUnits() - 1) > 1e-6
                or not np.allclose((transform[1], transform[2], transform[4], transform[5]), (70, 0, 0, -70))):
            raise RuntimeError("Use L2T original em UTM, pixel 70 m; nao L2G/HDF ou imagens reamostradas.")
        if lst.GetRasterBand(1).DataType not in (gdal.GDT_Float32, gdal.GDT_Float64):
            raise RuntimeError("L2T LST deve estar em Kelvin Float32; DN inteiro requer conversao documentada distinta.")
        for key in ("QC", "cloud"):
            ds = datasets[key]
            if (ds is None or ds.RasterCount != 1 or ds.RasterXSize != lst.RasterXSize or ds.RasterYSize != lst.RasterYSize
                    or ds.GetGeoTransform() != transform or not ds.GetSpatialRef().IsSame(crs)):
                raise RuntimeError("LST, QC e cloud precisam da mesma grade/SRC, sem deslocamento.")
            if ds.GetRasterBand(1).DataType not in (gdal.GDT_Byte, gdal.GDT_UInt16):
                raise RuntimeError("QC/cloud devem ser mascaras inteiras, nao valores interpolados.")
        raw_path = Path(temporary) / "LST_ECOSTRESS_Celsius.tif"
        target = stack.enter_context(gdal.GetDriverByName("GTiff").Create(str(raw_path), lst.RasterXSize, lst.RasterYSize,
            1, gdal.GDT_Float32, options=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3"]))
        target.SetGeoTransform(transform)
        target.SetProjection(lst.GetProjection())
        target.GetRasterBand(1).SetNoDataValue(-9999)
        valid_count = 0
        band = lst.GetRasterBand(1)
        if (band.GetScale() not in (None, 1.0) or band.GetOffset() not in (None, 0.0)):
            raise RuntimeError("Calibracao L2T inesperada; nao aplicar escala de produto HDF ao GeoTIFF Kelvin.")
        for row in range(0, lst.RasterYSize, 512):
            _check_cancel(feedback)
            height = min(512, lst.RasterYSize - row)
            values = lst.ReadAsArray(0, row, lst.RasterXSize, height)
            qc = datasets["QC"].ReadAsArray(0, row, lst.RasterXSize, height)
            cloud = datasets["cloud"].ReadAsArray(0, row, lst.RasterXSize, height)
            valid = np.isfinite(values) & (values > 0) & (values != band.GetNoDataValue()) & (cloud == 0) & ((qc & 15) == 0)
            result = np.full(values.shape, -9999, dtype=np.float32)
            result[valid] = values[valid] - 273.15
            valid_count += int(np.count_nonzero(valid))
            target.GetRasterBand(1).WriteArray(result, 0, row)
        if not valid_count:
            raise RuntimeError("Nenhum pixel ECOSTRESS valido apos QC/cloud. Escolha outra aquisicao.")
        coverage = valid_count / (lst.RasterXSize * lst.RasterYSize)
        _embed_metadata(target, {"TITLE": "NASA ECOSTRESS LST Celsius (70 m)", "INDEX": "LST",
            "INDEX_UNIT": "Celsius", "INPUT_BANDS": json.dumps({key: str(path) for key, path in inputs.items()}),
            "SOURCE": "NASA/JPL ECOSTRESS L2T LSTE", "SOURCE_URL": "https://doi.org/10.5067/ECOSTRESS/ECO_L2T_LSTE.003",
            "ACQUISITION_GRANULE": next(iter(granules)), "OUTPUT_RESOLUTION_M": 70, "NODATA_VALUE": -9999,
            "METHOD_VERSION": VERSION, "VALID_FRACTION": coverage, "CRS": crs.ExportToWkt(),
            "METHOD_REFERENCE": METHOD_REFERENCES["ecostress"], "PROCESSING": "LST Kelvin - 273,15; cloud=0; QC bits 0-3=0000; grade 70 m sem interpolacao.",
            "ABSTRACT": "Temperatura de superficie instantanea, nao do ar. QC/cloud nao eliminam toda incerteza. "
                "Cobertura parcial permanece NoData; sem fusao, preenchimento ou super-resolucao. Validacao independente necessaria.",
            "RIGHTS": "NASA/JPL-Caltech; atribuir produto e DOI.", "LICENSE": "NASA Earthdata open data"}, "LST Celsius")
        target.FlushCache()
        stack.close()
        prepared = _prepare_product_grid(raw_path, temporary, "lst", mask=mask, feedback=feedback)
        result = _write_processing_output(prepared, output, True, "lst", feedback, method, class_count, manual_limits)
        report_path = _product_report(result, "lst", temporary, feedback, method)
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        report.update(source="NASA ECOSTRESS L2T", granule=next(iter(granules)), inputs={key: str(path) for key, path in inputs.items()},
            reference=METHOD_REFERENCES["ecostress"], valid_fraction_native=coverage,
            thermal_note="cloud=0; QC bits 0-3=0000. Bits de precisao/emissividade nao filtrados. LST nao e temperatura do ar.")
        Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        return str(result), report_path


def generate_drone_product(bands, product, output, feedback=None, classification="equal", class_count=5,
                           manual_limits="", savi_l=0.5, scale=1.0, offset=0.0, mask=None,
                           target_crs=None, product_details=None):
    """Gera indice a partir de bandas de um ortomosaico local georreferenciado."""
    product = (product or "").lower()
    if product not in DRONE_PRODUCTS:
        raise RuntimeError("Produto de drone invalido.")
    if not np.isfinite((scale, offset)).all() or scale <= 0:
        raise RuntimeError("Escala deve ser positiva e escala/deslocamento devem ser finitos.")
    _classification_limits(classification, class_count, manual_limits)
    required = {"vari": ("green", "red", "blue"), "ndvi": ("nir", "red"),
                "savi": ("nir", "red"), "ndwi": ("green", "nir")}[product]
    missing = [key for key in required if key not in bands]
    if missing:
        raise RuntimeError(f"Faltam bandas para {product.upper()}: {', '.join(missing)}.")
    output = Path(output)
    with TemporaryDirectory(prefix="qgis_drone_") as folder:
        temporary = Path(folder)
        prepared = {}
        for key in required:
            path, band_number = bands[key]
            source = gdal.Open(str(path))
            if source is None:
                raise RuntimeError(f"Nao foi possivel abrir a banda {key}: {path}")
            if not source.GetProjection():
                raise RuntimeError(f"A banda {key} nao possui SRC; gere um ortomosaico georreferenciado.")
            if not 1 <= band_number <= source.RasterCount:
                raise RuntimeError(f"Numero da banda {key} invalido: {band_number}.")
            spatial_ref = source.GetSpatialRef()
            if spatial_ref is None or not spatial_ref.IsProjected():
                raise RuntimeError("Use ortomosaico em SRC projetado, com unidade linear e GSD definido.")
            selected = source.GetRasterBand(band_number)
            extracted = temporary / f"{key}.tif"
            translated = gdal.Translate(str(extracted), source, bandList=[band_number],
                creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"])
            if translated is None:
                raise RuntimeError(f"Falha ao preparar a banda {key}.")
            translated.Close()
            with gdal.Open(str(extracted), gdal.GA_Update) as destination:
                target = destination.GetRasterBand(1)
                nodata = selected.GetNoDataValue()
                mask_band = selected.GetMaskBand()
                valid_pixels = nonzero_pixels = 0
                value_min, value_max = float("inf"), float("-inf")
                for row in range(0, source.RasterYSize, 512):
                    height = min(512, source.RasterYSize - row)
                    values = target.ReadAsArray(0, row, source.RasterXSize, height)
                    invalid = mask_band.ReadAsArray(0, row, source.RasterXSize, height) == 0
                    if nodata is not None:
                        invalid |= values == nodata
                    values[invalid] = 0
                    target.WriteArray(values, 0, row)
                    valid_pixels += int(np.count_nonzero(~invalid))
                    nonzero_pixels += int(np.count_nonzero(values))
                    if (~invalid).any():
                        value_min = min(value_min, float(values[~invalid].min()))
                        value_max = max(value_max, float(values[~invalid].max()))
                target.SetNoDataValue(0)
                target.SetScale(float(scale))
                target.SetOffset(float(offset))
            if feedback is not None:
                feedback.pushInfo(f"Banda {key}/{band_number}: {valid_pixels} pixels cobertos; {nonzero_pixels} nao zero; faixa {value_min:g}-{value_max:g}.")
            gt = source.GetGeoTransform()
            resolution = max(abs(gt[1]), abs(gt[5])) * spatial_ref.GetLinearUnits()
            source.Close()
            prepared[key] = (resolution, extracted)
        _, _, staged, _ = _create_spectral_index("drone", product, prepared,
            _raster_bounds(prepared[required[0]][1]), temporary, temporary, feedback, savi_l=savi_l)
        staged = _prepare_product_grid(staged, temporary, product, mask, target_crs, feedback)
        result = str(_write_processing_output(staged, output, True, product, feedback,
                                               classification, class_count, manual_limits))
    with gdal.Open(result, gdal.GA_Update) as dataset:
        dataset.SetMetadataItem("DRONE_SCALE_FACTOR", str(scale))
        dataset.SetMetadataItem("DRONE_ADDITIVE_OFFSET", str(offset))
        dataset.SetMetadataItem("INPUT_RASTERS", json.dumps({key: str(bands[key][0]) for key in required}))
        dataset.SetMetadataItem("LIMITATION", "VARI usa apenas RGB e nao substitui NDVI; indices NIR exigem reflectancia calibrada.")
    report = _product_report(result, product, Path(next(iter(bands.values()))[0]).parent, feedback, classification)
    if product_details is not None:
        product_details["report"] = report
    if feedback is not None:
        feedback.setProgress(100)
        feedback.pushInfo(f"Produto de drone pronto: {result}")
    return result


def generate_analytic_product(source, bands_directory, product, composition, output, feedback=None,
                              classification="equal", class_count=5, manual_limits="",
                              fusion_fill_gaps=False, fusion_min_r2=0.2, fusion_details=None, savi_l=0.5,
                              mask=None, target_crs=None, thermal_interval=False, product_details=None):
    """Cria um produto a partir das bandas locais, sem nova consulta ou download."""
    source = (source or "").lower()
    product = (product or "rgb").lower()
    cutline = mask
    if thermal_interval:
        if product not in {"lst", "lst_fused"}:
            raise RuntimeError("Intervalos de 1 Celsius sao exclusivos de temperatura.")
        classification = "celsius"
    directory = Path(bands_directory)
    if source not in MULTIBAND_SOURCES:
        raise RuntimeError("Selecione uma fonte multibanda válida.")
    if product not in ANALYTIC_PRODUCTS:
        raise RuntimeError("Produto analítico inválido.")
    if product not in {"rgb", "composition"}:
        _classification_limits(classification, class_count, manual_limits)
    if not directory.is_dir():
        raise RuntimeError(f"Pasta das bandas não encontrada: {directory}")
    bands = _saved_bands(source, directory)
    if not bands:
        raise RuntimeError("Nenhuma banda compatível foi encontrada na pasta selecionada.")
    if feedback is not None:
        feedback.setProgressText("Gerando produto a partir das bandas já baixadas...")
        feedback.setProgress(10)

    if product == "lst_fused":
        if not isinstance(fusion_fill_gaps, bool):
            raise RuntimeError("O preenchimento de lacunas deve ser autorizado por um booleano.")
        if not isinstance(fusion_min_r2, (float, int)) or not np.isfinite(fusion_min_r2) or not 0 <= fusion_min_r2 <= 1:
            raise RuntimeError("R2 minimo da fusao deve estar entre 0 e 1.")
        if not (directory / MANIFEST_NAME).is_file():
            raise RuntimeError("Fusao exige bandas com manifesto de coerencia; baixe novamente.")
        destination = _product_output_path(output, product)
        owned = []
        try:
            with TemporaryDirectory(prefix="qgis_fusion_") as folder:
                temporary = Path(folder)
                original, fused, mask, report = _create_fused_lst(
                    source, directory, bands, temporary, fusion_fill_gaps, fusion_min_r2, feedback)
                original = _prepare_product_grid(original, temporary, "lst", cutline, target_crs, feedback)
                fused = _prepare_product_grid(fused, temporary, "lst_fused", cutline, target_crs, feedback)
                mask = _prepare_product_grid(mask, temporary, "mask", cutline, target_crs, feedback)
                report["output_processing"] = {"cutline": cutline, "target_crs": target_crs,
                    "note": "Validacao do modelo refere-se a grade nativa; recorte/reprojecao nao sao validacao fina."}
                original_output = _write_processing_output(original, destination.with_name(destination.stem + "_original.tif"),
                                                            True, "lst", feedback, classification, class_count, manual_limits)
                owned.extend((original_output, original_output.with_suffix(".qml")))
                mask_output = _write_processing_output(mask, destination.with_name(destination.stem + "_estimativa_mask.tif"), product="mask", feedback=feedback)
                owned.append(mask_output)
                report["original_lst"] = str(original_output)
                report["provenance_mask"] = str(mask_output)
                staged_report = temporary / "fusao.json"
                staged_report.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                destination.parent.mkdir(parents=True, exist_ok=True)
                report_output = _commit_output(staged_report, destination.with_name(destination.stem + "_metodo.json"), "report")
                owned.append(report_output)
                with gdal.Open(str(fused), gdal.GA_Update) as dataset:
                    dataset.SetMetadataItem("ORIGINAL_LST", str(original_output))
                    dataset.SetMetadataItem("PROVENANCE_MASK", str(mask_output))
                    dataset.SetMetadataItem("METHOD_REPORT", str(report_output))
                result = str(_write_processing_output(fused, destination, True, product, feedback, classification, class_count, manual_limits))
        except Exception:
            for path in owned:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    if feedback is not None:
                        feedback.pushWarning(f"Artefato incompleto nao removido por bloqueio: {path}")
            raise
        if fusion_details is not None:
            fusion_details.update(original_lst=str(original_output), provenance_mask=str(mask_output), method_report=str(report_output))
        audit = _product_report(result, product, directory, feedback)
        if product_details is not None:
            product_details["report"] = audit
        if feedback is not None:
            feedback.pushInfo(f"LST estimada: {result}; original: {original_output}; mascara: {mask_output}; metodo: {report_output}.")
            feedback.setProgress(100)
        return result
    if product == "rgb":
        source_path = _create_rgb_preview(source, bands, directory)
    elif product == "composition":
        labels = {
            "natural": "RGB natural",
            "vegetation": "Falsa cor - vegetação",
            "urban": "Falsa cor - áreas construídas",
        }
        source_path = _create_rgb_preview(
            source,
            bands,
            directory,
            _composition_assets(source, composition),
            f"RGB_{composition}.vrt",
            f"{labels.get(composition, 'Composição RGB')} — {MULTIBAND_SOURCES[source]['title']}",
            METHOD_REFERENCES["composition"],
        )
    else:
        if not (directory / MANIFEST_NAME).is_file():
            raise RuntimeError("Bandas antigas sem manifesto de coerencia; baixe novamente antes de calcular indices.")
        if product not in SPECTRAL_INDICES[source]:
            raise RuntimeError(f"{product.upper()} não está disponível para {MULTIBAND_SOURCES[source]['title']}.")
        reference = next(iter(bands.values()))[1]
        with TemporaryDirectory(prefix="qgis_product_", dir=directory) as temporary:
            _, _, source_path, _ = _create_spectral_index(
                source,
                product,
                bands,
                _raster_bounds(reference),
                Path(temporary),
                Path(temporary),
                feedback,
                savi_l=savi_l,
            )
            source_path = _prepare_product_grid(source_path, temporary, product, cutline, target_crs, feedback)
            result = str(_write_processing_output(source_path, output, True, product, feedback,
                                                   classification, class_count, manual_limits))
        audit = _product_report(result, product, directory, feedback)
        if product_details is not None:
            product_details["report"] = audit
        if feedback is not None:
            feedback.setProgress(100)
            feedback.pushInfo(f"Produto pronto: {result}")
        return result
    if source_path is None:
        raise RuntimeError("As bandas necessárias para o produto escolhido não estão disponíveis.")
    with TemporaryDirectory(prefix="qgis_rgb_grid_", dir=directory) as temporary:
        source_path = _prepare_product_grid(source_path, temporary, product, cutline, target_crs, feedback)
        written_output = _write_processing_output(source_path, output, False, product, feedback)
    if written_output is not None:
        if feedback is not None and Path(written_output) != Path(output):
            feedback.pushWarning(
                f"O arquivo de saída estava aberto; o produto foi salvo em {written_output}."
            )
        output = str(written_output)
    if feedback is not None:
        feedback.setProgress(100)
        feedback.pushInfo(f"Produto pronto: {output}")
    return str(output)


def _run_multiband(
    project, bounds_web, clip_label, source, year, max_cloud, feedback=None,
    output_directory=None, stretch_contrast=False, spectral_index=None,
    processing_output=None, result_details=None, download_workers=ANALYTIC_DOWNLOAD_WORKERS,
    month_start=1, month_end=12, band_profile="complete",
):
    config = MULTIBAND_SOURCES[source]
    selected_bands = _download_band_specs(source, band_profile)
    web_crs = QgsCoordinateReferenceSystem("EPSG:3857")
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    extent = QgsCoordinateTransform(web_crs, wgs84, project.transformContext()).transformBoundingBox(QgsRectangle(*bounds_web))
    bounds_wgs84 = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    token_state = {"collection": config["collection"], "source": source}
    if config["catalog"] == PLANETARY_COMPUTER_SEARCH_URL:
        token_state["token"] = _planetary_computer_token(config["collection"])
    if feedback is not None:
        feedback.setProgressText(f"Consultando cenas {config['title']}...")
    search = _search_multiband(bounds_wgs84, year, max_cloud, config, source, token_state.get("token"), feedback,
                               month_start, month_end)
    if search is None:
        return None
    scenes, same_date, year = search
    if token_state.get("token"):
        token_state["token"] = _planetary_computer_token(config["collection"])
    reference = None
    for item in scenes:
        _check_cancel(feedback)
        try:
            reference = gdal.Open(_asset_path(item["assets"]["red"]["href"], token_state.get("token")))
        except RuntimeError:
            continue
        if reference is not None:
            break
    if reference is None:
        raise RuntimeError("Nao foi possivel abrir uma banda de referencia; verifique a conexao e a disponibilidade do catalogo.")
    try:
        crs = reference.GetProjection()
        origin = reference.GetGeoTransform()
    finally:
        reference.Close()
    native_crs = QgsCoordinateReferenceSystem(crs)
    original_crs = native_crs
    # Alguns COGs USGS usam UTM norte e northing negativo no hemisferio sul.
    # A mudanca de false northing conserva a grade e evita SRC fora da area de uso.
    code = native_crs.authid().removeprefix("EPSG:")
    if code.isdigit() and 32601 <= int(code) <= 32660 and bounds_wgs84[3] < 0:
        native_crs = QgsCoordinateReferenceSystem(f"EPSG:{int(code) + 100}")
        anchor = QgsCoordinateTransform(original_crs, native_crs, project.transformContext()).transform(QgsPointXY(origin[0], origin[3]))
        origin = (anchor.x(), origin[1], origin[2], anchor.y(), origin[4], origin[5])
        crs = native_crs.toWkt()
    if not native_crs.isValid() or native_crs.isGeographic() or native_crs.mapUnits() != Qgis.DistanceUnit.Meters:
        raise RuntimeError("A grade analitica precisa de um SRC projetado metrico valido.")
    native_extent = QgsCoordinateTransform(web_crs, native_crs, project.transformContext()).transformBoundingBox(QgsRectangle(*bounds_web))
    finest = 10 if source == "l2a" else 30
    alignment = max(band[2] for band in config["bands"])
    bounds = (
        origin[0] + floor((native_extent.xMinimum() - origin[0]) / alignment) * alignment,
        origin[3] + floor((native_extent.yMinimum() - origin[3]) / alignment) * alignment,
        origin[0] + ceil((native_extent.xMaximum() - origin[0]) / alignment) * alignment,
        origin[3] + ceil((native_extent.yMaximum() - origin[3]) / alignment) * alignment,
    )
    pixels = round((bounds[2] - bounds[0]) / finest) * round((bounds[3] - bounds[1]) / finest)
    if pixels > MAX_PIXELS:
        raise RuntimeError("Extensao analitica excede 250 milhoes de pixels; divida a area em recortes menores.")
    date_label = scenes[0]["properties"]["datetime"][:10] if same_date else f"{year}_multidata"
    output_directory = _product_output_path(
        output_directory or Path(project.fileName()).resolve().parent / f"{config['folder']}_{date_label}", "bandas"
    )
    output_directory.mkdir(parents=True, exist_ok=False)
    if feedback is not None:
        feedback.pushInfo(f"Grade analitica: {native_crs.authid()}, {finest} m; DN sem interpolacao.")
        feedback.pushInfo(f"Download analitico: ate {download_workers} bandas em paralelo; selecao de cenas sequencial.")
    outputs = {}
    band_coverage = {}
    with TemporaryDirectory(prefix="qgis_multiband_", dir=output_directory) as folder:
        temporary = Path(folder)
        observations, choice_path, coverage = _prepare_observations(
            scenes, source, bounds, crs, finest, temporary, token_state, feedback, download_workers,
            required_keys=None if band_profile == "complete" else tuple(
                band[0] for band in selected_bands if band[0] not in {"scl", "qa_pixel", "fmask"}),
        )
        dates = sorted({obs["scene"]["properties"]["datetime"][:10] for obs in observations})
        for position, (key, band_name, resolution, _) in enumerate(selected_bands, 1):
            _check_cancel(feedback)
            if feedback is not None:
                feedback.setProgressText(f"Montando {band_name} ({resolution} m)...")
                feedback.pushInfo(f"Banda {position}/{len(selected_bands)}: {band_name}.")
            mosaic = _compose_observation_band(
                observations, choice_path, source, key, resolution, bounds, crs, temporary, token_state, feedback,
            )
            if mosaic is None:
                if feedback is not None:
                    feedback.pushWarning(f"{band_name} indisponivel; nao sera incluida no manifesto.")
                continue
            mosaic_path, (scale, offset) = mosaic
            path = output_directory / f"{band_name}_{resolution}m.tif"
            with gdal.Translate(
                str(path), str(mosaic_path),
                options=gdal.TranslateOptions(
                    format="GTiff", creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"],
                    callback=_gdal_progress(feedback),
                ),
            ) as dataset:
                band_coverage[key] = float(dataset.GetMetadataItem("BAND_VALID_FRACTION"))
                if feedback is not None and key in {"lwir", "lwir11"}:
                    feedback.pushInfo(f"Cobertura termica {band_name}: {band_coverage[key]:.2%}; cobertura optica: {coverage:.2%}.")
                    if band_coverage[key] < 0.98:
                        feedback.pushWarning(f"{band_name} incompleta: {1 - band_coverage[key]:.2%} sem dados. {LST_NODATA_NOTE}")
                _embed_metadata(dataset, {
                    "IDENTIFIER": path.stem, "TITLE": f"{config['title']} {band_name}",
                    "SOURCE": config["source"], "SOURCE_URL": config["catalog"],
                    "PROCESSING_LEVEL": config["level"], "BAND_NAME": band_name,
                    "CRS": native_crs.authid(), "OUTPUT_RESOLUTION_M": resolution,
                    "ACQUISITION_DATES": ",".join(dates), "NODATA_VALUE": _asset_nodata(source, key),
                    "SCALE_FACTOR": scale, "ADDITIVE_OFFSET": offset,
                    "METHOD_VERSION": VERSION, "COHERENT_OBSERVATIONS": "YES",
                    "OPTICAL_VALID_FRACTION": coverage,
                    "BAND_VALID_FRACTION": band_coverage[key],
                    "PROCESSING": "Grade UTM alinhada; vizinho mais proximo; selecao comum por pixel; DN sem interpolacao.",
                    "METHOD_REFERENCE": METHOD_REFERENCES["hls" if source.startswith("hls_") else "quality"],
                    "RIGHTS": config["rights"], "LICENSE": config["license"],
                }, band_name)
                _build_overviews(dataset, "NEAREST", feedback)
            outputs[key] = (resolution, path)
            if feedback is not None:
                feedback.setProgress(20 + position * 65 / len(selected_bands))
        scene_index = output_directory / "CENA_POR_PIXEL.tif"
        with gdal.Translate(str(scene_index), str(choice_path), creationOptions=["TILED=YES", "COMPRESS=DEFLATE"]) as dataset:
            _build_overviews(dataset, "NEAREST", feedback)
        manifest = {
            "version": VERSION, "source": source, "crs": native_crs.authid(), "download_profile": band_profile,
            "optical_valid_fraction": coverage, "coherent_observations": True,
            "search_months": [month_start, month_end], "search_year": year,
            "scene_index": scene_index.name,
            "scenes": [{"index": obs["index"], "id": obs["scene"]["id"], "date": obs["scene"]["properties"]["datetime"]}
                       for obs in observations],
            "bands": {key: {"resolution": resolution, "file": path.name, "valid_fraction": band_coverage[key]}
                      for key, (resolution, path) in outputs.items()},
        }
        (output_directory / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        (output_directory / "CENAS_STAC.json").write_text(json.dumps(manifest["scenes"], indent=2), encoding="utf-8")
        if spectral_index:
            _, _, preview, _ = _create_spectral_index(source, spectral_index, outputs, bounds, temporary, temporary, feedback)
        else:
            preview = _create_rgb_preview(source, outputs, output_directory)
        if preview is None:
            raise RuntimeError("As bandas RGB necessarias nao foram produzidas.")
        with gdal.Open(str(preview), gdal.GA_Update) as dataset:
            dataset.SetMetadataItem("SATELITE_BANDS_FOLDER", str(output_directory))
        result = _write_processing_output(preview, processing_output, bool(spectral_index), spectral_index or "rgb", feedback)
        if result is None:
            result = preview
            if spectral_index:
                result = _write_processing_output(preview, output_directory / f"{spectral_index.upper()}.tif", True, spectral_index, feedback)
    (output_directory / REFERENCES_NAME).write_text(
        f"{config['title']}\nSRC: {native_crs.authid()}\nCobertura optica real: {coverage:.1%}\n"
        f"Datas: {', '.join(dates)}\nValores: DN sem interpolacao; escala e offset por asset no GeoTIFF.\n"
        f"Metodologia: {MULTIBAND_METHODOLOGY}\n{METHOD_REFERENCES_TEXT}\n"
        f"Licenca: {config['license']}. {config['rights']}\n", encoding="utf-8",
    )
    if result_details is not None:
        result_details.update(bands_folder=str(output_directory), source=source, coverage=coverage)
    if feedback is not None:
        feedback.setProgress(100)
        feedback.pushInfo(f"Concluido: {len(outputs)} bandas em {output_directory}")
    elif iface is not None:
        layer = _rgb_preview_layer(result, config["title"]) if not spectral_index else QgsRasterLayer(str(result), spectral_index.upper())
        project.addMapLayer(layer)
        iface.setActiveLayer(layer)
        iface.zoomToActiveLayer()
    return str(result)


@gdal.config_options(NETWORK_OPTIONS)
def run(
    source=None,
    year=None,
    access_token=None,
    mask_name=None,
    zoom=None,
    max_cloud=None,
    stretch_contrast=False,
    spectral_index=None,
    extent=None,
    mask=None,
    output=None,
    feedback=None,
    context=None,
    result_details=None,
    download_workers=ANALYTIC_DOWNLOAD_WORKERS,
    month_start=1,
    month_end=12,
    band_profile="complete",
):
    gdal.UseExceptions()
    # Configurações de rede para acelerar a busca dos milhares de blocos XYZ:
    # HTTP/2 multiplexado reaproveita uma única conexão TCP para muitas
    # requisições pequenas (ganho grande em latência), e os fragmentos são
    # baixados em paralelo (ver CHUNK_WORKERS e _download_chunks).
    if context is not None:
        project = QgsProject()
        project.setTransformContext(context.transformContext())
    else:
        project = QgsProject.instance()
    if not project.fileName() and not output:
        raise RuntimeError("Salve o projeto QGIS antes de executar o script.")

    web_crs = QgsCoordinateReferenceSystem("EPSG:3857")
    clip_label = "extensão informada"
    if extent is None:
        polygon_layers = sorted(
            (
                layer
                for layer in project.mapLayers().values()
                if layer.type() == Qgis.LayerType.Vector
                and layer.geometryType() == Qgis.GeometryType.Polygon
            ),
            key=lambda layer: layer.name().lower(),
        )
        if not polygon_layers:
            raise RuntimeError("O projeto não possui camada vetorial poligonal para recorte.")
        if mask is None and mask_name is None:
            active = iface.activeLayer()
            mask = active if active in polygon_layers else polygon_layers[0]
        elif mask is None:
            matches = [
                layer
                for layer in polygon_layers
                if layer.name() == mask_name or layer.id() == mask_name
            ]
            if not matches:
                raise RuntimeError(f"Camada poligonal não encontrada no projeto: {mask_name}")
            mask = matches[0]
        if not mask.crs().isValid() or mask.extent().isEmpty():
            raise RuntimeError("A camada escolhida não possui CRS/extensão válidos.")
        if feedback is None:
            iface.setActiveLayer(mask)
        transform = QgsCoordinateTransform(mask.crs(), web_crs, project.transformContext())
        extent = transform.transformBoundingBox(mask.extent())
        clip_label = f'camada "{mask.name()}"'
    if extent.isEmpty():
        raise RuntimeError("A extensão de recorte está vazia ou inválida.")
    bounds = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())

    source = (source or "cloudless").lower()

    if source in MULTIBAND_SOURCES:
        config = MULTIBAND_SOURCES[source]
        year = year or None
        if year is not None and year not in config["years"]:
            raise RuntimeError(f"Ano {config['title']} inválido. Escolha um destes: {config['years']}.")
        max_cloud = 20 if max_cloud is None else max_cloud
        if not 0 <= max_cloud <= 100:
            raise RuntimeError("O limite de nuvens deve estar entre 0 e 100%.")
        spectral_index = (spectral_index or "").lower() or None
        if spectral_index and spectral_index not in SPECTRAL_INDICES[source]:
            raise RuntimeError(f"Índice {spectral_index.upper()} indisponível para {config['title']}.")
        processing_output = Path(output).expanduser().resolve() if output else None
        multiband_directory = processing_output.with_suffix("") if processing_output else None
        return _run_multiband(
            project,
            bounds,
            clip_label,
            source,
            year,
            max_cloud,
            feedback,
            multiband_directory,
            stretch_contrast,
            spectral_index,
            processing_output,
            result_details,
            download_workers,
            month_start,
            month_end,
            band_profile,
        )
    elif source == "cloudless":
        year = year or CLOUDLESS_YEARS[0]
        if year not in CLOUDLESS_YEARS:
            raise RuntimeError(f"Ano inválido. Escolha um destes: {CLOUDLESS_YEARS}.")
        source_zooms = CLOUDLESS_ZOOMS
        selectable_zooms = _available_zooms(bounds, source_zooms)
        if not selectable_zooms:
            raise RuntimeError("A área é grande demais até para a menor resolução Cloudless.")
        zoom = zoom or selectable_zooms[0]
        if zoom not in source_zooms:
            raise RuntimeError(f"Zoom Cloudless inválido. Escolha um destes: {source_zooms}.")
        source_url = CLOUDLESS_URL_TEMPLATE.replace("{year}", str(year))
        source_name = f"Sentinel-2 cloudless {year}"
        source_copyright = CLOUDLESS_COPYRIGHT
        temporary_prefix = "qgis_sentinel2_cloudless_"
    elif source == "esri":
        source_zooms = ESRI_ZOOMS
        selectable_zooms = _available_zooms(bounds, source_zooms)
        if not selectable_zooms:
            raise RuntimeError("A área é grande demais até para a menor resolução Esri.")
        zoom = zoom or selectable_zooms[0]
        if zoom not in source_zooms:
            raise RuntimeError(f"Zoom Esri inválido. Escolha um destes: {source_zooms}.")
        if not access_token:
            raise RuntimeError("A opção Esri exige uma autenticação salva no QGIS.")
        access_token = access_token.strip()
        if not access_token:
            raise RuntimeError("A opção Esri exige uma API key válida.")
        source_url = f"{ESRI_URL}?token={quote(access_token, safe='')}"
        source_name = "Esri World Imagery"
        source_copyright = ESRI_COPYRIGHT
        temporary_prefix = "qgis_esri_world_imagery_"
    elif source == "google":
        source_zooms = GOOGLE_ZOOMS
        selectable_zooms = _available_zooms(bounds, source_zooms)
        zoom = zoom or selectable_zooms[0]
        if zoom not in source_zooms:
            raise RuntimeError(f"Zoom Google inválido. Escolha um destes: {source_zooms}.")
        source_url = GOOGLE_URL_TEMPLATE
        source_name = "Google Satellite"
        source_copyright = GOOGLE_COPYRIGHT
        temporary_prefix = "qgis_google_satellite_"
    elif source == "bing":
        source_zooms = BING_ZOOMS
        selectable_zooms = _available_zooms(bounds, source_zooms)
        zoom = zoom or selectable_zooms[0]
        if zoom not in source_zooms:
            raise RuntimeError(f"Zoom Bing inválido. Escolha um destes: {source_zooms}.")
        source_url = BING_URL_TEMPLATE
        source_name = "Bing Virtual Earth"
        source_copyright = BING_COPYRIGHT
        temporary_prefix = "qgis_bing_virtual_earth_"
    else:
        choices = "', '".join(("cloudless", *MULTIBAND_SOURCES, "esri", "google", "bing"))
        raise RuntimeError(f"Fonte inválida. Use '{choices}'.")

    plan = _download_plan(bounds, zoom)
    zoom = plan["zoom"]
    resolution = plan["resolution"]
    total_columns = plan["total_columns"]
    total_rows = plan["total_rows"]
    total_pixels = plan["total_pixels"]
    if total_pixels > MAX_PIXELS:
        estimated_gib = total_pixels * 4 / (1024**3)
        warning = (
            f"O zoom escolhido produzirá aproximadamente {total_pixels:,} pixels "
            f"({estimated_gib:.1f} GiB sem compressão). A operação pode exigir muita RAM, "
            "espaço em disco e tráfego de rede."
        )
        if feedback is not None:
            feedback.pushWarning(warning)
        else:
            print(f"Aviso: {warning}")
    grid_columns = plan["grid_columns"]
    grid_rows = plan["grid_rows"]
    parts = plan["parts"]

    # A busca dos tiles agora usa o minidriver GDAL_WMS/TMS diretamente (mesma
    # família de ferramentas já usada no modo Sentinel-2 L2A), com HTTP/2
    # multiplexado e vários fragmentos simultâneos — bem mais rápido do que
    # o provedor XYZ/WMS interno do QGIS usado na versão anterior.
    connection = (
        _virtual_earth_connection(zoom)
        if source == "bing"
        else _tms_connection_string(source_url, zoom)
    )

    if source == "cloudless":
        folder_prefix = f"Imagem_Satelite_Sentinel2_Cloudless_{year}_Z{zoom}"
        output_prefix = f"sentinel2_cloudless_{year}_z{zoom}"
        final_name = f"Sentinel-2 cloudless {year} — Z{zoom} ({resolution:.2f} m/pixel)"
    elif source == "esri":
        folder_prefix = f"Imagem_Satelite_Esri_Z{zoom}"
        output_prefix = f"esri_world_imagery_z{zoom}"
        final_name = f"Esri World Imagery — Z{zoom} ({resolution:.2f} m/pixel)"
    elif source == "google":
        folder_prefix = f"Imagem_Satelite_Google_Z{zoom}"
        output_prefix = f"google_satellite_z{zoom}"
        final_name = f"Google Satellite — Z{zoom} ({resolution:.2f} m/pixel)"
    else:
        folder_prefix = f"Imagem_Satelite_Bing_Virtual_Earth_Z{zoom}"
        output_prefix = f"bing_virtual_earth_z{zoom}"
        final_name = f"Bing Virtual Earth — Z{zoom} ({resolution:.2f} m/pixel)"
    automatic_output = output is None
    output_directory = Path(output).expanduser().resolve().parent if output else (
        Path(project.fileName()).resolve().parent / folder_prefix
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    output = Path(output).expanduser().resolve() if output else (
        output_directory / f"{output_prefix}_recortada.tif"
    )
    message = (
        f"{source_name}: zoom {zoom}, {resolution:.3f} m/pixel, "
        f"{grid_columns} x {grid_rows} fragmentos, até {CHUNK_WORKERS} em paralelo; "
        f"estimativa bruta: {total_pixels * 4 / 1024**3:.2f} GB."
    )
    if feedback is not None:
        feedback.pushInfo(message)
    else:
        print(message)

    with TemporaryDirectory(prefix=temporary_prefix, dir=output.parent) as temporary:
        temporary = Path(temporary)

        download = _download_chunks(connection, parts, resolution, temporary, feedback)
        if download is None:
            return None
        chunks, empty_count = download

        while source == "esri" and empty_count == len(parts):
            fallback_zoom = next((value for value in source_zooms if value < zoom), None)
            if fallback_zoom is None:
                break
            if fallback_zoom is not None:
                if feedback is not None:
                    feedback.pushWarning(
                        f"A Esri não possui cobertura em Z{zoom} nesta área; tentando Z{fallback_zoom}."
                    )
                zoom = fallback_zoom
                plan = _download_plan(bounds, zoom)
                resolution = plan["resolution"]
                total_columns = plan["total_columns"]
                total_rows = plan["total_rows"]
                total_pixels = plan["total_pixels"]
                grid_columns = plan["grid_columns"]
                grid_rows = plan["grid_rows"]
                parts = plan["parts"]
                connection = _tms_connection_string(source_url, zoom)
                folder_prefix = f"Imagem_Satelite_Esri_Z{zoom}"
                output_prefix = f"esri_world_imagery_z{zoom}"
                final_name = f"Esri World Imagery — Z{zoom} ({resolution:.2f} m/pixel)"
                if automatic_output:
                    output_directory = output_directory.parent / folder_prefix
                    output_directory.mkdir(parents=True, exist_ok=True)
                    output = output_directory / f"{output_prefix}_recortada.tif"
                download = _download_chunks(connection, parts, resolution, temporary, feedback)
                if download is None:
                    return None
                chunks, empty_count = download

        if empty_count == len(parts):
            raise RuntimeError("A fonte nao forneceu nenhum pixel de imagem valido nesta extensao e nos niveis tentados.")

        if feedback is not None:
            feedback.setProgressText("Montando e recortando o mosaico...")
            feedback.setProgress(75)

        vrt = temporary / "mosaico.vrt"
        with gdal.BuildVRT(
            str(vrt), chunks,
            options=gdal.BuildVRTOptions(resolution="highest", addAlpha=True, srcNodata="0 0 0", resampleAlg="lanczos"),
        ) as dataset:
            if dataset.RasterCount < 3:
                raise RuntimeError("O mosaico XYZ não contém as três bandas RGB esperadas.")
            enhanced = temporary / "mosaico_visual.tif"
            with gdal.Translate(
                str(enhanced), dataset,
                options=gdal.TranslateOptions(
                    format="GTiff", outputType=gdal.GDT_Byte, bandList=[1, 2, 3, 4],
                    xRes=resolution, yRes=resolution, resampleAlg="lanczos",
                    creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"],
                    callback=_gdal_progress(feedback),
                ),
            ):
                pass
        staged_output = temporary / output.name
        with gdal.Translate(
            str(staged_output), str(enhanced),
            options=gdal.TranslateOptions(
                format="GTiff", projWin=[bounds[0], bounds[3], bounds[2], bounds[1]],
                creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"],
                callback=_gdal_progress(feedback),
            ),
        ) as clipped:
            for band in range(1, 5):
                clipped.GetRasterBand(band).DeleteNoDataValue()
            alpha = clipped.GetRasterBand(4)
            alpha.SetColorInterpretation(gdal.GCI_AlphaBand)
            for row in range(0, clipped.RasterYSize, 512):
                _check_cancel(feedback)
                height = min(512, clipped.RasterYSize - row)
                for column in range(0, clipped.RasterXSize, 512):
                    width = min(512, clipped.RasterXSize - column)
                    rgb = clipped.ReadAsArray(column, row, width, height)[:3]
                    values = alpha.ReadAsArray(column, row, width, height)
                    values[np.all(rgb == 0, axis=0)] = 0
                    alpha.WriteArray(values, column, row)
            alpha = None
        _check_cancel(feedback)

        output = _commit_output(staged_output, output, "rgb")

    dataset = gdal.Open(str(output), gdal.GA_Update)
    if dataset is None:
        raise RuntimeError(f"Não foi possível gravar os metadados no GeoTIFF: {output}")
    if source == "cloudless":
        acquisition = f"composição anual {year}"
        product = "EOX Sentinel-2 Cloudless"
        cloud = "nuvens minimizadas; sem limiar garantido"
        source_url = "https://cloudless.eox.at/"
        license_text = "Creative Commons Attribution 4.0; contém dados Copernicus Sentinel modificados"
    elif source == "esri":
        acquisition = "variável conforme o local; consultar metadados Esri"
        product = "Esri World Imagery"
        cloud = "não filtrado"
        source_url = "https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9"
        license_text = "Termos de uso da Esri e dos provedores identificados no serviço"
    elif source == "google":
        acquisition = "variável conforme o local; consultar o serviço Google"
        product = "Google Satellite"
        cloud = "não filtrado"
        source_url = "https://developers.google.com/maps/documentation/tile/overview"
        license_text = "Termos de uso e atribuição do serviço Google"
    else:
        acquisition = "variável conforme o local; consultar o serviço Microsoft"
        product = "Bing Virtual Earth"
        cloud = "não filtrado"
        source_url = "https://learn.microsoft.com/en-us/bingmaps/rest-services/imagery/get-imagery-metadata"
        license_text = "Termos de uso e atribuição do serviço Microsoft Bing"
    _embed_metadata(
        dataset,
        {
            "IDENTIFIER": output.stem,
            "TITLE": final_name,
            "ABSTRACT": f"Mosaico RGB de {product}, recortado pela {clip_label}.",
            "KEYWORDS": f"imagem de satélite;RGB;{product};mosaico",
            "SOURCE": source_name,
            "SOURCE_URL": source_url,
            "PRODUCT": product,
            "ACQUISITION_PERIOD": acquisition,
            "CLOUD_INFORMATION": cloud,
            "ZOOM": zoom,
            "OUTPUT_PIXEL_SIZE_M": f"{resolution:.6f}",
            "BANDS": "RGB + alpha",
            "CRS": "EPSG:3857",
            "CLIP_EXTENT": clip_label,
            "METHODOLOGY": RGB_METHODOLOGY,
            "PROCESSING": f"Download em {len(parts)} fragmentos ({CHUNK_WORKERS} em paralelo via GDAL/TMS), mosaico GDAL, reamostragem Lanczos e recorte pela {clip_label}.",
            "RIGHTS": source_copyright,
            "LICENSE": license_text,
        },
    )
    dataset = None
    if feedback is not None:
        feedback.setProgress(95)

    overview_dataset = None
    try:
        overview_dataset = gdal.Open(str(output), gdal.GA_Update)
        if overview_dataset is None:
            raise RuntimeError("o GeoTIFF não pôde ser reaberto")
        _build_overviews(overview_dataset, feedback=feedback)
        overview_dataset.Close()
        overview_dataset = None
    except Exception as exc:
        overview_dataset = None
        print(f"Aviso: não foi possível gerar as pirâmides de {output.name}: {exc}")

    if source == "cloudless":
        reference_text = f"""REFERÊNCIAS E ATRIBUIÇÃO — SENTINEL-2 CLOUDLESS {year}

CRÉDITO CURTO PARA O MAPA
Fonte: EOX Cloudless — contém dados Copernicus Sentinel modificados {year}.

DESCRIÇÃO TÉCNICA
Imagem: mosaico anual Sentinel-2 Cloudless {year}
Período: composição de observações do ano {year}; não é uma cena de uma única data
Resolução nominal da imagem-fonte: 10 m
Produto exportado: zoom {zoom}, aproximadamente {resolution:.3f} m/pixel em EPSG:3857
Processamento: mosaico GDAL, reamostragem Lanczos e recorte pela {clip_label}.
Metodologia: {RGB_METHODOLOGY}

FONTES OFICIAIS
EOX Sentinel-2 Cloudless:
https://cloudless.eox.at/

Serviço WMTS utilizado:
https://tiles.maps.eox.at/wmts/1.0.0/WMTSCapabilities.xml

Dados Copernicus Sentinel:
https://dataspace.copernicus.eu/

Arquivo final:
{output}
"""
    elif source == "esri":
        reference_text = f"""REFERÊNCIAS E ATRIBUIÇÃO — ESRI WORLD IMAGERY

CRÉDITO CURTO PARA O MAPA
Fonte: Esri, Vantor, Earthstar Geographics e GIS User Community.

DESCRIÇÃO TÉCNICA
Imagem: Esri World Imagery
Data da cena: consulte os metadados do serviço no local; o basemap é multitemporal
Produto exportado: zoom {zoom}, aproximadamente {resolution:.3f} m/pixel em EPSG:3857
Processamento: mosaico GDAL, reamostragem Lanczos e recorte pela {clip_label}.
Metodologia: {RGB_METHODOLOGY}

FONTES OFICIAIS
Esri World Imagery:
https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9

Serviço utilizado:
https://tiledbasemaps.arcgis.com/arcgis/rest/services/World_Imagery/MapServer

Metadados e citações:
https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/4

Arquivo final:
{output}
"""
    elif source == "google":
        reference_text = f"""REFERÊNCIAS E ATRIBUIÇÃO — GOOGLE SATELLITE

CRÉDITO CURTO PARA O MAPA
Fonte: Google Satellite; consulte os termos e a atribuição do serviço.

DESCRIÇÃO TÉCNICA
Imagem: Google Satellite
Data da cena: consulte os metadados do serviço no local; o basemap é multitemporal
Produto exportado: zoom {zoom}, aproximadamente {resolution:.3f} m/pixel em EPSG:3857
Processamento: mosaico GDAL, reamostragem Lanczos e recorte pela {clip_label}.
Metodologia: {RGB_METHODOLOGY}

FONTES OFICIAIS
Google Maps Platform — Map Tiles:
https://developers.google.com/maps/documentation/tile/overview

Arquivo final:
{output}
"""
    else:
        reference_text = f"""REFERÊNCIAS E ATRIBUIÇÃO — BING VIRTUAL EARTH

CRÉDITO CURTO PARA O MAPA
Fonte: Microsoft Bing Virtual Earth; consulte os termos e a atribuição do serviço.

DESCRIÇÃO TÉCNICA
Imagem: Bing Virtual Earth
Data da cena: consulte os metadados do serviço no local; o basemap é multitemporal
Produto exportado: zoom {zoom}, aproximadamente {resolution:.3f} m/pixel em EPSG:3857
Processamento: mosaico GDAL, reamostragem Lanczos e recorte pela {clip_label}.
Metodologia: {RGB_METHODOLOGY}

FONTES OFICIAIS
Bing Maps — Imagery Metadata:
https://learn.microsoft.com/en-us/bingmaps/rest-services/imagery/get-imagery-metadata

Serviço de tiles utilizado:
https://ecn.t3.tiles.virtualearth.net/tiles/

Arquivo final:
{output}
"""
    references = output_directory / REFERENCES_NAME
    references.write_text(reference_text, encoding="utf-8")

    if feedback is not None:
        feedback.setProgress(100)
        feedback.pushInfo(f"Concluido: {output}")
        return str(output)
    final = QgsRasterLayer(str(output), final_name)
    if not final.isValid():
        raise RuntimeError(f"O resultado foi salvo, mas não pôde ser carregado: {output}")
    final.setScaleBasedVisibility(False)
    if stretch_contrast:
        final.setContrastEnhancement(
            QgsContrastEnhancement.ContrastEnhancementAlgorithm.StretchToMinimumMaximum,
            Qgis.RasterRangeLimit.MinimumMaximum,
        )
    project.addMapLayer(final)
    _apply_layer_metadata(final)
    if iface is not None:
        iface.setActiveLayer(final)
        canvas = iface.mapCanvas()
        canvas.setExtent(
            QgsCoordinateTransform(final.crs(), canvas.mapSettings().destinationCrs(), project)
            .transformBoundingBox(final.extent())
        )
        canvas.refresh()
        iface.messageBar().pushSuccess("Satelite Cadente", f"Concluído: {output.name}")
    print(f"Concluído: {output}")
    return str(output)


if __name__ in {"__main__", "__console__"}:
    print("Importe o script e chame run(); para geoprocessamento, escolha Sentinel-2 L2A multibanda.")

