"""Regressoes isoladas; executar com o Python do QGIS, nunca no projeto aberto."""
import ast
import json
import sys
import argparse
import io
import hashlib
import threading
import time
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import numpy as np
from osgeo import gdal, gdal_array, osr
from qgis.core import (QgsApplication, QgsProject, QgsProcessingContext, QgsProcessingFeedback,
                       QgsRectangle, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsMapSettings, QgsMapRendererSequentialJob)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor

from imagens_satellite import satellite_mosaic_qgis as engine


def raster(path, values, scale=None, offset=None, epsg=32724, pixel=30, dtype=gdal.GDT_UInt16, nodata=0):
    values = np.asarray(values, dtype=gdal_array.GDALTypeCodeToNumericTypeCode(dtype))
    ds = gdal.GetDriverByName("GTiff").Create(str(path), values.shape[1], values.shape[0], 1, dtype)
    ds.SetGeoTransform((500000, pixel, 0, 8700000 if epsg == 32724 else -1300000, 0, -pixel))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(epsg)
    ds.SetProjection(crs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    if scale is not None:
        band.SetScale(scale)
        band.SetOffset(offset)
    band.WriteArray(values)
    band = None
    ds.Close()
    return path


def check_render_at_scales(path, classified=False):
    from qgis.core import QgsRasterLayer
    layer = QgsRasterLayer(str(path), "indice classificado") if classified else engine._rgb_preview_layer(path, "teste de zoom")
    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setOutputSize(QSize(256, 256))
    settings.setBackgroundColor(QColor("white"))
    target = QgsCoordinateReferenceSystem("EPSG:31984")
    settings.setDestinationCrs(target)
    extent = QgsCoordinateTransform(layer.crs(), target, QgsProject()).transformBoundingBox(layer.extent())
    for factor in (1, 4, 0.75):
        area = QgsRectangle(extent)
        area.scale(factor)
        settings.setExtent(area)
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        result = job.renderedImage()
        assert not result.isNull()
        colors = {result.pixelColor(x, y).rgb() for x in range(0, 256, 4) for y in range(0, 256, 4)}
        assert len(colors) > (1 if classified else 8), (factor, colors)
    return 3


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="satelite_test_")
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_top_menu_routes_algorithms_and_unloads_without_residue(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from qgis.PyQt import sip
        from qgis.PyQt.QtCore import QCoreApplication, QEvent
        from qgis.PyQt.QtWidgets import QMainWindow, QMenu
        from imagens_satellite.plugin import SateliteCadentePlugin

        window = QMainWindow()
        project_menu = window.menuBar().addMenu("Projeto")
        help_menu = window.menuBar().addMenu("Ajuda")
        baseline = window.menuBar().actions()
        toolbar = window.addToolBar("Complementos")
        messages = SimpleNamespace(pushCritical=Mock())
        iface = SimpleNamespace(mainWindow=lambda: window,
            firstRightStandardMenu=lambda: help_menu,
            addToolBarIcon=toolbar.addAction, removeToolBarIcon=toolbar.removeAction,
            messageBar=lambda: messages)
        plugin = SateliteCadentePlugin(iface)
        registry = QgsApplication.processingRegistry()
        try:
            for anchor in (help_menu, None):
                iface.firstRightStandardMenu = lambda: anchor
                plugin.initGui()
                menu = plugin.menu
                plugin.initGui()
                self.assertIs(plugin.menu, menu)
                self.assertEqual(menu.title(), "Satelite Cadente")
                self.assertTrue(menu.menuAction().icon().isNull())
                self.assertEqual(len(window.menuBar().actions()), len(baseline) + 1)
                self.assertIs(window.menuBar().actions()[1 if anchor else -1], menu.menuAction())
                self.assertEqual(len(toolbar.actions()), 1)
                self.assertEqual([m.title() for m in plugin.submenus],
                    ["Obter imagens", "Gerar produtos"])
                self.assertEqual([sum(a.isSeparator() for a in m.actions())
                    for m in [menu, *plugin.submenus]], [0, 1, 1])
                self.assertEqual([[a.data() for a in m.actions() if not a.isSeparator()]
                    for m in plugin.submenus], [
                        ["imagens_satellite:mosaico_satellite", "imagens_satellite:ecostress_buscar", "imagens_satellite:cbers4a_buscar_cobertura",
                         "imagens_satellite:cbers4a_wpm"],
                        ["imagens_satellite:produto_analitico", "imagens_satellite:ecostress_lst", "imagens_satellite:drone_ortomosaico_indices",
                         "imagens_satellite:cbers4a_toa", "imagens_satellite:cbers4a_mosaico_toa"]])
                actions = [a for m in plugin.submenus for a in m.actions() if not a.isSeparator()]
                expected = {a.id() for a in registry.providerById("imagens_satellite").algorithms()}
                self.assertEqual({a.data() for a in actions}, expected)
                self.assertEqual(len(actions), 9)
                self.assertEqual(len({a.objectName() for a in actions}), 9)
                self.assertTrue(all(not a.icon().isNull() and a.toolTip() for a in actions))
                self.assertTrue(all(not m.icon().isNull() for m in plugin.submenus))
                with patch("processing.execAlgorithmDialog") as launch:
                    for action in actions:
                        action.trigger()
                        launch.assert_called_with(action.data())
                with patch("processing.execAlgorithmDialog", side_effect=RuntimeError("teste")):
                    plugin.action.trigger()
                messages.pushCritical.assert_called_with("Satelite Cadente", "teste")
                old_objects = [menu, *plugin.submenus, *actions]
                plugin.unload()
                plugin.unload()
                self.assertEqual(window.menuBar().actions(), baseline)
                self.assertEqual(toolbar.actions(), [])
                self.assertIsNone(registry.providerById("imagens_satellite"))
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertTrue(all(sip.isdeleted(obj) for obj in old_objects))
                self.assertFalse(sip.isdeleted(project_menu))
            with patch.object(iface, "firstRightStandardMenu", side_effect=RuntimeError("falha de UI")):
                with self.assertRaisesRegex(RuntimeError, "falha de UI"):
                    plugin.initGui()
            self.assertIsNone(plugin.menu)
            self.assertIsNone(registry.providerById("imagens_satellite"))
            self.assertEqual(toolbar.actions(), [])
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertFalse(any(m.objectName().startswith("imagens_satellite_")
                for m in window.findChildren(QMenu)))
        finally:
            plugin.unload()
            window.close()
            window.deleteLater()

    def test_nasa_hls_signed_nodata_fmask_and_calibration(self):
        from imagens_satellite import satellite_mosaic_qgis as nasa
        from imagens_satellite.provider import _product_values, SOURCE_VALUES, ANALYTIC_SOURCE_VALUES
        self.assertEqual(SOURCE_VALUES[7:], ("landsat89", "hls_l30", "hls_s30"))
        self.assertEqual(ANALYTIC_SOURCE_VALUES[3:], ("landsat89", "hls_l30", "hls_s30"))
        for source in ("hls_l30", "hls_s30"):
            self.assertNotIn("lst", _product_values(source))
            self.assertNotIn("lst_fused", _product_values(source))
            self.assertEqual(nasa._band_calibration(source, "red"), (0.0001, 0.0))
            self.assertIn("fmask", [spec[0] for spec in nasa._download_band_specs(source, "vegetation")])
            mask = np.array([0, 1, 2, 4, 8, 16, 32, 64, 128, 192, 255], dtype=np.uint8)
            np.testing.assert_array_equal(nasa._quality_invalid(source, mask),
                [False, False, True, True, True, True, False, False, False, True, True])
        self.assertEqual(dict((key, name) for key, name, _, _ in nasa.HLS_S30_BANDS)["nir08"], "B8A")
        self.assertEqual(nasa._scene_tile({"id": "HLS.S30.T24LWM.2026001T130000.v2.0", "properties": {}}), "T24LWM")
        paths = {}
        for key, values in (("red", [[2000, 0, -9999, -500]]), ("nir08", [[6000, 6000, 6000, 6000]])):
            path = raster(self.directory / (key + ".tif"), values, 0.0001, 0, dtype=gdal.GDT_Int16, nodata=-9999)
            paths[key] = (30, path)
        label, _, result, _ = nasa._create_spectral_index("hls_s30", "ndvi", paths, None, self.directory, self.directory)
        with gdal.Open(str(result)) as dataset:
            np.testing.assert_allclose(dataset.ReadAsArray(), [[0.5, 1, -9999, -9999]], atol=1e-6)
            self.assertEqual(dataset.GetMetadataItem("CODE_REPOSITORY"), "https://github.com/Radkzin/satelite-cadente")

    def test_ecostress_lst_kelvin_qc_cloud_and_same_granule(self):
        from imagens_satellite import satellite_mosaic_qgis as nasa
        name = "ECOv003_L2T_LSTE_41217_015_24LVM_20251012T194021_02"
        paths = {}
        for key, array, dtype, nodata in (("LST", [[300, 301, 302, 0, 305]], gdal.GDT_Float32, float("nan")),
                ("QC", [[0, 1, 4, 0, 0]], gdal.GDT_UInt16, None),
                ("cloud", [[0, 0, 0, 0, 1]], gdal.GDT_Byte, 255)):
            path = self.directory / (name + "_" + key + ".tif")
            ds = gdal.GetDriverByName("GTiff").Create(str(path), 5, 1, 1, dtype)
            crs = osr.SpatialReference()
            crs.ImportFromEPSG(32724)
            ds.SetProjection(crs.ExportToWkt())
            ds.SetGeoTransform((500000, 70, 0, 8700000, 0, -70))
            if nodata is not None:
                ds.GetRasterBand(1).SetNoDataValue(nodata)
            ds.GetRasterBand(1).WriteArray(np.array(array))
            ds.Close()
            paths[key] = path
        result, report = nasa.generate_ecostress_lst(paths["LST"], paths["QC"], paths["cloud"], self.directory / "out.tif")
        with gdal.Open(result) as dataset:
            np.testing.assert_allclose(dataset.ReadAsArray(), [[26.85, -9999, -9999, -9999, -9999]], atol=1e-4)
            self.assertEqual(dataset.GetGeoTransform()[1], 70)
        self.assertEqual(json.loads(Path(report).read_text(encoding="utf-8"))["source"], "NASA ECOSTRESS L2T")
        self.assertTrue(Path(result).with_suffix(".qml").is_file())
        with self.assertRaisesRegex(RuntimeError, "mesmo granulo"):
            nasa.generate_ecostress_lst(paths["LST"], paths["QC"], self.directory / "ECOv003_L2T_LSTE_other_cloud.tif", self.directory / "bad.tif")
        with gdal.Open(str(paths["QC"]), gdal.GA_Update) as ds:
            ds.SetGeoTransform((500001, 70, 0, 8700000, 0, -70))
        with self.assertRaisesRegex(RuntimeError, "mesma grade"):
            nasa.generate_ecostress_lst(paths["LST"], paths["QC"], paths["cloud"], self.directory / "shifted.tif")

    def test_ecostress_public_catalog_and_redirect_never_leaks_token(self):
        from imagens_satellite import satellite_mosaic_qgis as nasa
        from urllib.request import Request
        name = "ECOv003_L2T_LSTE_test"
        assets = {key: f"https://{nasa.ECOSTRESS_DOWNLOAD_HOST}/lp-prod-protected/{name}_{key}.tif" for key in ("LST", "QC", "cloud")}
        payload = {"feed": {"entry": [{"title": name, "time_start": "2026-01-01T10:00:00Z",
            "links": [{"href": href} for href in assets.values()] + [{"href": "https://example.org/secret_LST.tif"}]}]}}
        with patch.object(nasa, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
            catalog, folder = nasa.search_ecostress((-39.1, -12.3, -38.9, -12.1), "2026-01-01", "2026-01-02", self.directory)
        self.assertIsNone(folder)
        saved = json.loads(Path(catalog).read_text(encoding="utf-8"))
        self.assertEqual(saved["scenes"][0]["assets"], assets)
        request = Request(assets["LST"], headers={"Authorization": "Bearer secret"})
        handler = nasa._EarthdataRedirect()
        redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://storage.example.org/asset")
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaisesRegex(RuntimeError, "TLS"):
            handler.redirect_request(request, None, 302, "Found", {}, "http://storage.example.org/asset")

    def test_source_not_deleted(self):
        source = raster(self.directory / "same.tif", [[100]])
        with self.assertRaises((ValueError, RuntimeError)):
            engine._write_processing_output(source, source)
        self.assertTrue(source.is_file())

    def test_cbers_mosaic_keeps_observations_coherent(self):
        folders=[]
        for i in (1,2):
            folder=self.directory/str(i);folder.mkdir();folders.append(folder)
            outputs={}
            for key in ('BAND1','BAND2','BAND3','BAND4','ndvi','savi','ndwi'):
                data=[[float(i),float(i)],[float(i),-9999.]]
                if i==1 and key=='BAND4': data[0][0]=-9999.
                path=raster(folder/(key+'.tif'),data,pixel=8,dtype=gdal.GDT_Float32,nodata=-9999)
                with gdal.Open(str(path),gdal.GA_Update) as ds:
                    ds.SetMetadata({'CALIBRATION':'TOA_EXPERIMENTAL','SCENE_ID':str(i)})
                outputs[key]=str(path)
            (folder/'CALIBRACAO_TOA.json').write_text(json.dumps({'calibration':'TOA_EXPERIMENTAL',
                'scene_id':str(i),'savi_l':.5,'outputs':outputs,
                'coefficients':{k:{'esun':1000} for k in ('BAND1','BAND2','BAND3','BAND4')}}))
        results=engine.mosaic_cbers_toa(folders,self.directory/'mosaic')
        for key in ('BAND3','BAND4','ndvi','SCENE_INDEX'):
            with gdal.Open(results[key]) as ds:
                np.testing.assert_array_equal(ds.ReadAsArray(),[[2,1],[1,-9999]])
        report=json.loads((self.directory/'mosaic'/'MOSAICO_CBers_TOA.json').read_text())
        self.assertEqual(report['coverage_in_rectangle'],.75)
        self.assertTrue(Path(results['ndvi']).with_suffix('.qml').exists())
        from osgeo import ogr
        mask_path=self.directory/'mask.gpkg'
        vector=ogr.GetDriverByName('GPKG').CreateDataSource(str(mask_path))
        srs=osr.SpatialReference();srs.ImportFromEPSG(32724)
        layer=vector.CreateLayer('limite',srs,ogr.wkbPolygon)
        feature=ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr.CreateGeometryFromWkt('POLYGON ((500000 8700000,500016 8700000,500016 8699984,500000 8699984,500000 8700000))'))
        layer.CreateFeature(feature);vector=None
        clipped=engine.mosaic_cbers_toa(folders,self.directory/'clipped',str(mask_path))
        coverage=json.loads((self.directory/'clipped'/'MOSAICO_CBers_TOA.json').read_text())['coverage_inside_polygon']
        self.assertAlmostEqual(float(coverage),.75)
        with self.assertRaises(RuntimeError): engine.mosaic_cbers_toa([folders[0],folders[0]],self.directory/'bad')

    def test_cbers_coverage_search_ranks_all_distinct_footprints(self):
        from osgeo import ogr
        boundary=ogr.CreateGeometryFromWkt('POLYGON ((0 0,10 0,10 10,0 10,0 0))')
        items=[]
        for ident,extent in [('a',(0,0,6,10)),('b',(4,0,10,10)),('c',(0,0,6,10))]:
            xmin,ymin,xmax,ymax=extent
            geom=ogr.CreateGeometryFromWkt(f'POLYGON (({xmin} {ymin},{xmax} {ymin},{xmax} {ymax},{xmin} {ymax},{xmin} {ymin}))')
            items.append({'id':ident,'geometry':json.loads(geom.ExportToJson()),
                'properties':{'datetime':'2025-01-01T00:00:00Z','path':'1','row':'2'}})
        with patch.object(engine,'_stac_features',return_value=items):
            result=engine.search_cbers_wpm_coverage(boundary,2020,2026)
        self.assertEqual(result['searched_items'],3)
        self.assertEqual([s['id'] for s in result['selected_scenes']],['a','b'])
        self.assertAlmostEqual(result['geometric_union_coverage'],1)
        with self.assertRaises(RuntimeError): engine.search_cbers_wpm_coverage(boundary,2027,2026)

    def test_cbers_toa_equations_and_xml_identity(self):
        folder = self.directory / 'dn'
        folder.mkdir()
        item = {'id': 'CBERS_TEST', 'collection': 'CB4A-WPM-L4-DN-1',
                'properties': {'datetime': '2025-01-04T12:00:00Z'}}
        outputs = {}
        for i in range(1, 5):
            key = f'BAND{i}'
            path = raster(folder / (key + '.tif'), [[0, 100 + i * 20], [1023, 200 + i * 20]], pixel=8)
            with gdal.Open(str(path), gdal.GA_Update) as ds:
                ds.SetMetadata({'SCENE_ID': item['id'], 'CALIBRATION': 'UNCALIBRATED_DN'})
            outputs[key] = str(path)
            xml = f'''<prdf xmlns="test"><satellite><name>CBERS</name><number>4A</number><instrument>WPM</instrument></satellite>
                <image><timeStamp><center>2025-01-04T12:00:00</center></timeStamp><sunPosition><elevation>30</elevation></sunPosition>
                <absoluteCalibrationCoefficient><band name="{i}">0.2</band></absoluteCalibrationCoefficient></image></prdf>'''
            (folder / f'CBERS_TEST_{key}.xml').write_text(xml)
        (folder / 'CBERS_METODOLOGIA.json').write_text(json.dumps({'scene': item, 'outputs': outputs,
            'calibration': 'UNCALIBRATED_DN'}))
        result = engine.calibrate_cbers_wpm(folder, self.directory / 'toa', (2000, 1900, 1600, 1100))
        factor = np.pi * 0.2 * (1 - .01672)**2 / .5
        red, nir = 160 * factor / 1600, 180 * factor / 1100
        with gdal.Open(result['ndvi']) as ds:
            data = ds.ReadAsArray()
            self.assertAlmostEqual(data[0, 1], (nir - red) / (nir + red), places=6)
            self.assertEqual(data[0, 0], -9999)
            self.assertEqual(data[1, 0], -9999)
        with gdal.Open(result['savi']) as ds:
            self.assertAlmostEqual(ds.ReadAsArray()[0, 1], 1.5 * (nir - red) / (nir + red + .5), places=6)
        self.assertTrue(Path(result['ndvi']).with_suffix('.qml').is_file())
        self.assertEqual(engine._cbers_xml_calibration(xml, 'BAND4', '2025-01-04'), (.2, 30))
        with self.assertRaises(RuntimeError):
            engine._cbers_xml_calibration(xml, 'BAND4', '2025-01-05')
        with self.assertRaises(RuntimeError):
            engine.calibrate_cbers_wpm(folder, self.directory / 'bad', (1, -1, 1, 1))

    def test_cbers_download_preserves_dn_and_blocks_uncalibrated_indices(self):
        source = raster(self.directory / "cbers.tif", np.full((20, 20), 123), pixel=8)
        item = {"id": "CBERS_TEST", "properties": {"datetime": "2025-01-01T00:00:00Z", "eo:cloud_cover": None},
                "assets": {f"BAND{i}": {"href": f"https://data.inpe.br/BAND{i}.tif"} for i in range(5)}}
        crs = QgsCoordinateReferenceSystem("EPSG:32724")
        extent = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject()).transformBoundingBox(
            QgsRectangle(500000, 8699840, 500160, 8700000))
        bounds = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
        item['geometry'] = {'type': 'Polygon', 'coordinates': [[[bounds[0], bounds[1]],
            [bounds[2], bounds[1]], [bounds[2], bounds[3]], [bounds[0], bounds[3]], [bounds[0], bounds[1]]]]}
        folder = self.directory / "download"
        with patch.object(engine, "_stac_features", return_value=[item]), patch.object(engine, "_asset_path", return_value=str(source)):
            engine.download_cbers_wpm(bounds, 2025, "", True, folder)
        report = json.loads((folder / "CBERS_METODOLOGIA.json").read_text())
        self.assertFalse(report["indices_enabled"])
        self.assertEqual(len(report["outputs"]), 5)
        for key, path in report["outputs"].items():
            with gdal.Open(path) as ds:
                self.assertEqual(abs(ds.GetGeoTransform()[1]), 2 if key == "BAND0" else 8)
                self.assertEqual(ds.GetMetadataItem("CALIBRATION"), "UNCALIBRATED_DN")
                self.assertEqual(set(np.unique(ds.ReadAsArray())) - {0}, {123})
        with self.assertRaises(RuntimeError):
            engine.download_cbers_wpm(bounds, 2025, "", False, folder)
        with self.assertRaises(RuntimeError):
            engine._cbers_asset_url({"assets": {"BAND1": {"href": "https://evil.test/file.tif"}}}, "BAND1")

    def test_lean_download_retains_quality_and_avoids_swir(self):
        keys = {band[0] for band in engine._download_band_specs('l2a', 'vegetation')}
        self.assertEqual(keys, {'red','green','blue','nir','scl'})
        self.assertIn('swir16', {band[0] for band in engine._download_band_specs('l2a','indices')})
        scenes = self.scenes()
        for scene in scenes:
            del scene['assets']['swir16']
            del scene['assets']['swir22']
        with patch.object(engine,'_asset_path',side_effect=lambda href,token=None: href):
            observations, _, coverage = engine._prepare_observations(scenes,'landsat89',
                (500000,8699880,500120,8700000),'EPSG:32724',30,self.directory,{},None,
                required_keys=('red','green','blue','nir08'))
        self.assertEqual(coverage,1)
        self.assertEqual(len(observations),2)

    def test_polygon_grid_thermal_classes_and_audit(self):
        from osgeo import ogr
        source = raster(self.directory/'thermal.tif', [[31.4,32.4,33.4],[34.4,35.4,36.4]],
                        dtype=gdal.GDT_Float32,nodata=-9999)
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        mask_path=self.directory/'polygon.gpkg'
        vector=ogr.GetDriverByName('GPKG').CreateDataSource(str(mask_path))
        crs=osr.SpatialReference(); crs.ImportFromEPSG(32724)
        layer=vector.CreateLayer('polygon',crs,ogr.wkbPolygon)
        feature=ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr.CreateGeometryFromWkt('POLYGON ((500000 8700000,500090 8700000,500000 8699940,500000 8700000))'))
        layer.CreateFeature(feature); vector=None
        result=engine._prepare_product_grid(source,self.directory,'lst',str(mask_path),'EPSG:31984')
        engine._classify_product(result,'lst','celsius')
        report=json.loads(Path(engine._product_report(result,'lst',self.directory)).read_text(encoding='utf-8'))
        self.assertEqual(report['classification']['method'],'celsius')
        with gdal.Open(str(result)) as ds:
            values=ds.ReadAsArray()
            self.assertEqual(ds.GetSpatialRef().GetAuthorityCode(None),'31984')
            self.assertEqual(ds.GetGeoTransform()[1],30)
            self.assertTrue(np.any(values==-9999))
            self.assertEqual(report['valid_pixels'],int(np.count_nonzero(values!=-9999)))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
        with self.assertRaises(RuntimeError):
            engine._classify_product(result,'ndvi','celsius')

    def test_failure_preserves_previous_output(self):
        source = raster(self.directory / "source.tif", [[100]])
        output = self.directory / "output.tif"
        output.write_bytes(b"previous")
        with patch.object(gdal, "Translate", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                engine._write_processing_output(source, output)
        self.assertEqual(output.read_bytes(), b"previous")

    def test_commit_never_overwrites_an_existing_result(self):
        one, two = self.directory / "stage1", self.directory / "stage2"
        one.write_bytes(b"first")
        two.write_bytes(b"second")
        wanted = self.directory / "result.tif"
        first = engine._commit_output(one, wanted, "rgb")
        second = engine._commit_output(two, wanted, "rgb")
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")

    def test_uint16_overviews_ignore_global_jpeg(self):
        source = raster(self.directory / "source.tif", np.full((64, 64), 1200))
        previous = gdal.GetConfigOption("COMPRESS_OVERVIEW")
        try:
            gdal.SetConfigOption("COMPRESS_OVERVIEW", "JPEG")
            output = engine._write_processing_output(source, self.directory / "output.tif")
            ds = gdal.Open(str(output))
            self.assertGreater(ds.GetRasterBand(1).GetOverviewCount(), 0)
            self.assertEqual(int(ds.GetRasterBand(1).GetOverview(0).ReadAsArray()[0, 0]), 1200)
            ds.Close()
            self.assertEqual(gdal.GetConfigOption("COMPRESS_OVERVIEW"), "JPEG")
        finally:
            gdal.SetConfigOption("COMPRESS_OVERVIEW", previous)

    def test_commit_between_drives(self):
        import errno
        source = self.directory / "staged.json"
        source.write_bytes(b'{"ok":true}')
        with patch.object(engine, "replace", side_effect=OSError(errno.EXDEV, "different drives")):
            output = engine._commit_output(source, self.directory / "result.json", "lst_fused")
        self.assertEqual(output.read_bytes(), b'{"ok":true}')
        self.assertFalse(source.exists())

    def test_run_has_no_production_self_check(self):
        tree = ast.parse(Path(engine.__file__).read_text(encoding="utf-8"))
        run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run")
        calls = [node.func.id for node in ast.walk(run) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        self.assertNotIn("_self_check", calls)

    def test_sentinel_asset_offset(self):
        asset = {"raster:bands": [{"scale": 0.0001, "offset": -0.1}]}
        self.assertEqual(engine._band_calibration("l2a", "red", asset), (0.0001, -0.1))

    def test_jenks_sample_maximum_does_not_add_an_extra_class(self):
        values = np.linspace(-0.8, 0.9, 512 * 512, dtype=np.float32).reshape(512, 512)
        source = raster(self.directory / "sampled.tif", values, dtype=gdal.GDT_Float32, nodata=-9999)
        engine._classify_product(source, "ndvi", "jenks", 5)
        from qgis.core import QgsRasterLayer
        layer = QgsRasterLayer(str(source), "sampled")
        layer.loadNamedStyle(str(source.with_suffix('.qml')))
        items = layer.renderer().shader().rasterShaderFunction().colorRampItemList()
        self.assertEqual(len(items), 5)
        self.assertTrue(np.isinf(items[-1].value))
        np.testing.assert_array_equal(gdal.Open(str(source)).ReadAsArray(), values)

    def test_savi_adjustment_reuses_grid_and_preserves_inputs(self):
        red = raster(self.directory / "red.tif", [[2000, 0], [2000, 2000]], 0.0001, 0)
        nir = raster(self.directory / "nir.tif", [[6000, 6000], [6000, 6000]], 0.0001, 0)
        original_bytes = [path.read_bytes() for path in (red, nir)]
        for soil in (0, 0.25, 0.5, 1):
            with patch.object(gdal, "Warp", wraps=gdal.Warp) as warp:
                _, _, output, _ = engine._create_spectral_index(
                    "l2a", "savi", {"red": (30, red), "nir": (30, nir)},
                    engine._raster_bounds(red), self.directory, self.directory, savi_l=soil)
                self.assertEqual(warp.call_count, 0)
            with gdal.Open(str(output)) as dataset:
                values = dataset.ReadAsArray()
                self.assertAlmostEqual(float(values[0, 0]), (1 + soil) * 0.4 / (0.8 + soil), places=6)
                self.assertEqual(values[0, 1], -9999)
                self.assertEqual(float(dataset.GetMetadataItem("SAVI_L")), soil)
        self.assertEqual(original_bytes, [path.read_bytes() for path in (red, nir)])
        for soil in (-1, 2, float("nan"), True, "0.5"):
            with self.assertRaisesRegex(RuntimeError, "Fator L"):
                engine._create_spectral_index("l2a", "savi", {}, (), self.directory, self.directory, savi_l=soil)

    def test_evi2_uses_calibrated_reflectance_and_relative_legend(self):
        from imagens_satellite import satellite_mosaic_qgis as plugin_engine

        red = raster(self.directory / "evi2_red.tif", [[2000, 0], [1000, 2000]], 0.0001, 0)
        nir = raster(self.directory / "evi2_nir.tif", [[6000, 6000], [5000, 8000]], 0.0001, 0)
        self.assertEqual(plugin_engine.ANALYTIC_PRODUCTS.index("lst_fused"), 7)
        self.assertEqual(plugin_engine.ANALYTIC_PRODUCTS.index("evi2"), 8)
        self.assertNotIn("evi2", plugin_engine.SPECTRAL_INDICES["drone"])
        for source, nir_key in (("l2a", "nir"), ("landsat89", "nir08")):
            _, _, output, _ = plugin_engine._create_spectral_index(
                source, "evi2", {"red": (30, red), nir_key: (30, nir)},
                plugin_engine._raster_bounds(red), self.directory, self.directory,
            )
            with gdal.Open(str(output)) as dataset:
                values = dataset.ReadAsArray()
                self.assertAlmostEqual(float(values[0, 0]), 1 / 2.08, places=6)
                self.assertEqual(values[0, 1], -9999)
                self.assertEqual(dataset.GetMetadataItem("INDEX"), "EVI2")
                self.assertIn("Jiang", dataset.GetMetadataItem("METHOD_REFERENCE"))
            plugin_engine._classify_product(output, "evi2", "equal", 5)
            from qgis.core import QgsRasterLayer
            layer = QgsRasterLayer(str(output), "EVI2")
            self.assertTrue(layer.loadNamedStyle(str(output.with_suffix(".qml")))[1])
            labels = [item.label for item in layer.renderer().shader().rasterShaderFunction().colorRampItemList()]
            self.assertTrue(all("relativo" in label for label in labels))
            del layer
        manifest = {"source": "landsat89", "coherent_observations": True,
                    "bands": {"red": {"resolution": 30, "file": red.name},
                              "nir08": {"resolution": 30, "file": nir.name}}}
        (self.directory / plugin_engine.MANIFEST_NAME).write_text(json.dumps(manifest))
        from imagens_satellite.provider import SateliteCadenteAnalyticProductAlgorithm
        algorithm = SateliteCadenteAnalyticProductAlgorithm()
        algorithm.initAlgorithm()
        feedback = QgsProcessingFeedback()
        result, successful = algorithm.run({
            "BANDS_FOLDER": str(self.directory), "SOURCE": 3, "PRODUCT": 8,
            "CLASSIFICATION": 0, "CLASS_COUNT": 5,
            "OUTPUT": str(self.directory / "evi2_provider.tif"),
        }, QgsProcessingContext(), feedback)
        self.assertTrue(successful, feedback.textLog())
        self.assertTrue(Path(result["OUTPUT"]).with_suffix(".qml").is_file())
        self.assertTrue(Path(result["REPORT"]).is_file())

    def test_drone_rgb_orthomosaic_generates_vari_and_nodata(self):
        path = self.directory / "ortomosaico_rgb.tif"
        ds = gdal.GetDriverByName("GTiff").Create(str(path), 2, 2, 3, gdal.GDT_Byte)
        ds.SetGeoTransform((500000, 0.05, 0, 8700000, 0, -0.05))
        crs = osr.SpatialReference()
        crs.ImportFromEPSG(32724)
        ds.SetProjection(crs.ExportToWkt())
        for index, (values, interpretation) in enumerate((
                ([[100, 0], [100, 100]], gdal.GCI_RedBand),
                ([[150, 0], [150, 150]], gdal.GCI_GreenBand),
                ([[50, 0], [50, 50]], gdal.GCI_BlueBand)), 1):
            band = ds.GetRasterBand(index)
            band.WriteArray(np.array(values, dtype=np.uint8))
            band.SetColorInterpretation(interpretation)
        ds.Close()
        source_hash = hashlib.sha256(path.read_bytes()).digest()
        from imagens_satellite.provider import DroneOrthomosaicAlgorithm
        algorithm = DroneOrthomosaicAlgorithm()
        algorithm.initAlgorithm()
        output = self.directory / "VARI.tif"
        feedback = QgsProcessingFeedback()
        result, successful = algorithm.run({"PRODUCT": 0, "RED": str(path), "RED_BAND": 1,
            "GREEN": str(path), "GREEN_BAND": 2, "BLUE": str(path), "BLUE_BAND": 3,
            "SCALE": 1, "OFFSET": 0, "CLASSIFICATION": 0, "CLASS_COUNT": 5,
            "OUTPUT": str(output)}, QgsProcessingContext(), feedback)
        self.assertTrue(successful, feedback.textLog())
        with gdal.Open(result["OUTPUT"]) as product:
            values = product.ReadAsArray()
            self.assertAlmostEqual(float(values[0, 0]), 0.25, places=6)
            self.assertEqual(values[0, 1], -9999)
            self.assertEqual(product.GetMetadataItem("INDEX"), "VARI")
        self.assertTrue(Path(result["OUTPUT"]).with_suffix(".qml").is_file())
        self.assertTrue(Path(result["REPORT"]).is_file())
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), source_hash)

    def test_month_filter_is_applied_to_catalog_and_returned_scenes(self):
        scenes = self.scenes()
        scenes[0]["properties"]["datetime"] = "2024-02-29T12:00:00Z"
        scenes[1]["properties"]["datetime"] = "2024-03-01T12:00:00Z"
        with patch.object(engine, "_stac_features", return_value=scenes) as catalog, \
                patch.object(engine, "_select_scenes_by_local_quality", return_value=([scenes[0]], True, 1)) as select:
            engine._search_multiband((0, 0, 1, 1), 2024, 20, engine.MULTIBAND_SOURCES["landsat89"],
                                    "landsat89", month_start=2, month_end=2)
            self.assertEqual(json.loads(catalog.call_args.args[0].data)["datetime"],
                             "2024-02-01T00:00:00Z/2024-02-29T23:59:59Z")
            self.assertEqual(select.call_args.args[0], [scenes[0]])
        for first, last in ((0, 12), (1, 13), (12, 1), (True, 12), (1.5, 12)):
            with self.assertRaisesRegex(RuntimeError, "Meses"):
                engine._search_multiband((), 2024, 20, {}, "landsat89", month_start=first, month_end=last)

    def test_sentinel_index_uses_saved_calibration(self):
        red = raster(self.directory / "red.tif", np.full((4, 4), 2000), 0.0001, -0.1)
        nir = raster(self.directory / "nir.tif", np.full((4, 4), 4000), 0.0001, -0.1)
        _, _, output, _ = engine._create_spectral_index(
            "l2a", "ndvi", {"red": (30, red), "nir": (30, nir)},
            engine._raster_bounds(red), self.directory, self.directory,
        )
        ds = gdal.Open(str(output))
        np.testing.assert_allclose(ds.ReadAsArray(), 0.5, atol=1e-6)
        self.assertTrue(ds.GetSpatialRef().IsSame(gdal.Open(str(red)).GetSpatialRef()))
        ds.Close()

    def test_mixed_resolution_index_ignores_overviews(self):
        values = np.full((4, 4), 2000, dtype=np.uint16)
        values[1::2, 1::2] = 6000
        nir = raster(self.directory / "nir.tif", values, 0.0001, 0, pixel=10)
        swir = raster(self.directory / "swir.tif", np.full((2, 2), 8000), 0.0001, 0, pixel=20)
        with gdal.Open(str(nir), gdal.GA_Update) as dataset:
            engine._build_overviews(dataset, "NEAREST")
        _, _, output, _ = engine._create_spectral_index("l2a", "ndbi", {"nir": (10, nir), "swir16": (20, swir)},
                                                       engine._raster_bounds(swir), self.directory, self.directory)
        with gdal.Open(str(output)) as dataset:
            np.testing.assert_allclose(dataset.ReadAsArray(), 1 / 7, atol=1e-6)

    def test_http_auth_error_is_not_nodata(self):
        with patch.object(gdal, "Translate", side_effect=RuntimeError("HTTP response code: 401")):
            with self.assertRaisesRegex(RuntimeError, "401|autentic"):
                engine._fetch_tms_window("connection", (0, 0, 1, 1), 1, 1, self.directory / "empty.tif")
        self.assertFalse((self.directory / "empty.tif").exists())

    def scenes(self, epsg=32724):
        keys = ("red", "green", "blue", "nir08", "swir16", "swir22", "qa_pixel")
        scenes = []
        for index in (1, 2):
            assets = {}
            for key in keys:
                values = np.full((4, 4), 64 if key == "qa_pixel" else index * 10000, dtype=np.uint16)
                if index == 1 and key == "nir08":
                    values[:, 2:] = 0
                path = raster(self.directory / f"scene{index}_{key}.tif", values, epsg=epsg)
                assets[key] = {"href": str(path)}
            scenes.append({"id": f"SCENE_{index}", "properties": {"datetime": f"2024-01-0{index}T00:00:00Z", "eo:cloud_cover": 0}, "assets": assets})
        return scenes

    def test_observations_are_common_and_first_scene_wins(self):
        scenes = self.scenes()
        temporary = self.directory / "staged"
        temporary.mkdir()
        bounds = (500000, 8699880, 500120, 8700000)
        crs = "EPSG:32724"
        with patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href):
            observations, choices, coverage = engine._prepare_observations(scenes, "landsat89", bounds, crs, 30, temporary, {}, None)
            self.assertEqual(coverage, 1.0)
            for key in ("red", "nir08"):
                path, _ = engine._compose_observation_band(observations, choices, "landsat89", key, 30, bounds, crs, temporary, {}, None)
                with gdal.Open(str(path)) as dataset:
                    np.testing.assert_array_equal(dataset.ReadAsArray(), np.tile([10000, 10000, 20000, 20000], (4, 1)))

    def test_quality_selection_retains_optical_fallback(self):
        scenes = self.scenes()
        scenes[1]["assets"].pop("red")
        with patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href), patch.object(engine, "_warp_remote_with_retry", side_effect=lambda destination, source, *args: gdal.Open(source)):
            selected, _, coverage = engine._select_scenes_by_local_quality(scenes, "landsat89", (0, 0, 1, 1))
        self.assertEqual(coverage, 1)
        self.assertEqual(len(selected), 2)
        temporary = self.directory / "staged"
        temporary.mkdir()
        with patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href):
            observations, _, coverage = engine._prepare_observations(selected, "landsat89", (500000, 8699880, 500120, 8700000),
                                                                    "EPSG:32724", 30, temporary, {}, None)
        self.assertEqual(observations[0]["scene"]["id"], "SCENE_1")
        self.assertEqual(coverage, 0.5)

    def test_quality_selection_stops_screening_after_98_percent(self):
        from imagens_satellite import satellite_mosaic_qgis as plugin_engine

        templates = self.scenes()
        scenes = []
        for index in range(16):
            template = templates[index % len(templates)]
            scenes.append({
                **template,
                "id": f"SCENE_{index}",
                "properties": {
                    **template["properties"],
                    "grid:code": "same-tile",
                    "datetime": f"2024-01-{index + 1:02d}T00:00:00Z",
                },
            })
        calls = []

        def open_quality(_destination, source, *args):
            calls.append(source)
            return gdal.Open(source)

        with patch.object(plugin_engine, "_asset_path", side_effect=lambda href, token=None: href), patch.object(
            plugin_engine, "_warp_remote_with_retry", side_effect=open_quality
        ):
            selected, _, coverage = plugin_engine._select_scenes_by_local_quality(
                scenes, "landsat89", (0, 0, 1, 1)
            )
        self.assertEqual(coverage, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(selected), 2)

    def test_xyz_mosaic_cleanup_after_translation_error(self):
        chunk = self.directory / "chunk.tif"
        with gdal.GetDriverByName("GTiff").Create(str(chunk), 4, 4, 3, gdal.GDT_Byte) as dataset:
            dataset.SetGeoTransform((0, 30, 0, 120, 0, -30))
            crs = osr.SpatialReference()
            crs.ImportFromEPSG(3857)
            dataset.SetProjection(crs.ExportToWkt())
            for band in range(1, 4):
                dataset.GetRasterBand(band).Fill(100)
        output = self.directory / "xyz.tif"
        with patch.object(engine, "_download_chunks", return_value=([str(chunk)], 0)), patch.object(gdal, "Translate", side_effect=RuntimeError("translate-failure")):
            with self.assertRaisesRegex(RuntimeError, "translate-failure"):
                engine.run(source="google", extent=QgsRectangle(0, 0, 120, 120), zoom=12,
                           output=str(output), feedback=QgsProcessingFeedback(), context=QgsProcessingContext())
        self.assertFalse(output.exists())
        self.assertFalse(list(self.directory.glob("qgis_google*")))

    def test_xyz_mosaic_uses_native_gdal_and_safe_output(self):
        chunk = self.directory / "tile.tif"
        with gdal.GetDriverByName("GTiff").Create(str(chunk), 4, 4, 3, gdal.GDT_Byte) as dataset:
            dataset.SetGeoTransform((0, 30, 0, 120, 0, -30))
            crs = osr.SpatialReference()
            crs.ImportFromEPSG(3857)
            dataset.SetProjection(crs.ExportToWkt())
            for band in range(1, 4):
                dataset.GetRasterBand(band).Fill(100)
        output = self.directory / "xyz.tif"
        output.write_bytes(b"previous")
        with patch.object(engine, "_download_chunks", return_value=([str(chunk)], 0)):
            actual = engine.run(source="google", extent=QgsRectangle(0, 0, 120, 120), zoom=12,
                                output=str(output), feedback=QgsProcessingFeedback(), context=QgsProcessingContext())
        self.assertEqual(output.read_bytes(), b"previous")
        with gdal.Open(actual) as dataset:
            self.assertEqual(dataset.RasterCount, 4)
            self.assertIsNone(dataset.GetRasterBand(1).GetNoDataValue())
            self.assertEqual(dataset.GetRasterBand(4).GetColorInterpretation(), gdal.GCI_AlphaBand)

    def test_manifest_ignores_stale_band(self):
        raster(self.directory / "SR_B4_30m.tif", [[1000]])
        raster(self.directory / "ST_B10_30m.tif", [[9999]])
        manifest = {"source": "landsat89", "coherent_observations": True, "bands": {"red": {"resolution": 30, "file": "SR_B4_30m.tif"}}}
        (self.directory / engine.MANIFEST_NAME).write_text(json.dumps(manifest))
        self.assertEqual(tuple(engine._saved_bands("landsat89", self.directory)), ("red",))

    def test_output_registration_moves_requested_path(self):
        from imagens_satellite.provider import _register_output
        context = QgsProcessingContext()
        project = QgsProject()
        context.setProject(project)
        wanted, actual = str(self.directory / "old.tif"), str(self.directory / "new.tif")
        context.addLayerToLoadOnCompletion(wanted, QgsProcessingContext.LayerDetails("image", project, "OUTPUT"))
        _register_output(context, wanted, actual)
        self.assertEqual(tuple(context.layersToLoadOnCompletion()), (actual,))
        self.assertEqual(context.layersToLoadOnCompletion()[actual].project, project)
        context.setLayersToLoadOnCompletion({})
        _register_output(context, wanted, actual)
        self.assertEqual(context.layersToLoadOnCompletion(), {})

    def test_multiband_does_not_change_project_and_retains_native_grid(self):
        self.check_multiband_native_grid(32724)

    def test_southern_landsat_normalizes_negative_northing(self):
        self.check_multiband_native_grid(32624)

    def check_multiband_native_grid(self, epsg):
        scenes = self.scenes(epsg)
        project = QgsProject()
        details = {}
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:32724"), QgsCoordinateReferenceSystem("EPSG:3857"), project)
        area = transform.transformBoundingBox(QgsRectangle(500000, 8699880, 500120, 8700000))
        bounds = (area.xMinimum(), area.yMinimum(), area.xMaximum(), area.yMaximum())
        with patch.object(engine, "_search_multiband", return_value=(scenes, False, 2024)), patch.object(engine, "_planetary_computer_token", return_value="mock"), patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href):
            output = engine._run_multiband(project, bounds, "test", "landsat89", 2024, 20,
                                          QgsProcessingFeedback(), self.directory / "bands", False, None,
                                          self.directory / "OUTPUT.tif", details)
        self.assertEqual(len(project.mapLayers()), 0)
        with gdal.Open(output) as dataset:
            self.assertEqual(dataset.GetSpatialRef().GetAuthorityCode(None), "32724")
            self.assertEqual(dataset.GetGeoTransform()[1], 30)
            self.assertEqual(dataset.GetMetadataItem("SATELITE_BANDS_FOLDER"), details["bands_folder"])
        product = engine.generate_analytic_product("landsat89", details["bands_folder"], "ndvi", "natural", self.directory / "NDVI.tif")
        with gdal.Open(product) as dataset:
            values = dataset.ReadAsArray()
            np.testing.assert_allclose(values[values != -9999], 0.0, atol=1e-6)
        for index in ("ndwi", "ndbi", "savi"):
            product = engine.generate_analytic_product("landsat89", details["bands_folder"], index, "natural", self.directory / f"{index}.tif")
            with gdal.Open(product) as dataset:
                values = dataset.ReadAsArray()
                np.testing.assert_allclose(values[values != -9999], 0.0, atol=1e-6)

    def test_legacy_sentinel_cog_is_rejected(self):
        self.assertEqual(engine.MULTIBAND_SOURCES["l2a"]["collection"], "sentinel-2-c1-l2a")
        with self.assertRaisesRegex(RuntimeError, "legado"):
            engine._band_calibration("l2a", "red", {"href": "https://sentinel-cogs.s3.amazonaws.com/sentinel-s2-l2a-cogs/B04.tif"})

    def test_missing_optional_band_does_not_use_another_date(self):
        scenes = self.scenes()
        scenes[1]["assets"]["lwir11"] = {"href": str(raster(self.directory / "thermal.tif", np.full((4, 4), 40000)))}
        temporary = self.directory / "staged"
        temporary.mkdir()
        bounds = (500000, 8699880, 500120, 8700000)
        with patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href):
            observations, choices, _ = engine._prepare_observations(scenes, "landsat89", bounds, "EPSG:32724", 30, temporary, {}, None)
            path, _ = engine._compose_observation_band(observations, choices, "landsat89", "lwir11", 30, bounds, "EPSG:32724", temporary, {}, None)
        with gdal.Open(str(path)) as dataset:
            np.testing.assert_array_equal(dataset.ReadAsArray(), np.tile([0, 0, 40000, 40000], (4, 1)))
        feedback = QgsProcessingFeedback()
        _, _, product, _ = engine._create_spectral_index("landsat89", "lst", {"lwir11": (30, path)}, bounds, temporary, self.directory, feedback)
        with gdal.Open(str(product)) as dataset:
            values = dataset.ReadAsArray()
            np.testing.assert_allclose(values[values != -9999], 40000 * 0.00341802 + 149 - 273.15, atol=1e-4)
            np.testing.assert_array_equal(values == -9999, np.tile([True, True, False, False], (4, 1)))
            self.assertEqual(dataset.GetMetadataItem("VALID_FRACTION"), "0.5")
            self.assertEqual(dataset.GetMetadataItem("MISSING_PIXELS"), "8")
            self.assertEqual(dataset.GetMetadataItem("METHOD_VERSION"), engine.VERSION)
        self.assertIn("Cobertura LST: 50.00%", feedback.textLog())
        self.assertIn("ASTER GED", feedback.textLog())
        self.assertEqual(feedback.textLog().count("pixels sem dados"), 1)

    def test_download_reports_thermal_coverage_separately(self):
        scenes = self.scenes()
        scenes[1]["assets"]["lwir11"] = {"href": str(raster(self.directory / "thermal.tif", np.full((4, 4), 40000)))}
        project = QgsProject()
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:32724"), QgsCoordinateReferenceSystem("EPSG:3857"), project)
        area = transform.transformBoundingBox(QgsRectangle(500000, 8699880, 500120, 8700000))
        bounds = (area.xMinimum(), area.yMinimum(), area.xMaximum(), area.yMaximum())
        folder = self.directory / "bands"
        feedback = QgsProcessingFeedback()
        with patch.object(engine, "_search_multiband", return_value=(scenes, False, 2024)), patch.object(engine, "_planetary_computer_token", return_value="mock"), patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href):
            engine._run_multiband(project, bounds, "test", "landsat89", 2024, 20, feedback,
                                 folder, processing_output=self.directory / "OUTPUT.tif")
        manifest = json.loads((folder / engine.MANIFEST_NAME).read_text(encoding="utf-8"))
        with gdal.Open(str(folder / "ST_B10_30m.tif")) as dataset:
            values = dataset.ReadAsArray()
            actual = np.count_nonzero(values) / values.size
        self.assertEqual(manifest["bands"]["lwir11"]["valid_fraction"], actual)
        self.assertLess(actual, manifest["optical_valid_fraction"])
        self.assertIn(f"Cobertura termica ST_B10: {actual:.2%}", feedback.textLog())
        self.assertEqual(feedback.textLog().count("ST_B10 incompleta"), 1)

    def test_cancellation_prevents_download(self):
        feedback = QgsProcessingFeedback()
        feedback.cancel()
        with patch.object(gdal, "Warp") as warp:
            with self.assertRaisesRegex(RuntimeError, "cancelado"):
                engine._warp_remote_with_retry(self.directory / "cancel.tif", "unused", None, "test", feedback)
            warp.assert_not_called()

    def test_tsharp_preserves_holes_and_conserves_coarse_radiance(self):
        rows, columns = np.indices((64, 64))
        ndvi = 0.1 + 0.8 * ((rows * 11 + columns * 7) % 101) / 100
        temperature = 20 + 25 * (1 - ndvi) ** 0.625
        ndvi[:4, :4] = 0
        temperature[20:28, 20:28] = -9999
        predicted, flags, report = engine._tsharp_scene(ndvi, temperature, np.ones(ndvi.shape, bool), 4)
        self.assertEqual(report["status"], "fitted")
        self.assertGreater(report["validation_r2"], 0.2)
        np.testing.assert_array_equal(predicted == -9999, temperature == -9999)
        self.assertEqual(set(np.unique(flags)), {0, 1, 2})
        for y in range(0, 64, 4):
            for x in range(0, 64, 4):
                original = temperature[y:y+4, x:x+4]
                result = predicted[y:y+4, x:x+4]
                if np.all(original != -9999):
                    self.assertAlmostEqual(float(np.mean((original + 273.15)**4)**0.25),
                                           float(np.mean((result + 273.15)**4)**0.25), places=4)
        filled, flags, report = engine._tsharp_scene(ndvi, temperature, np.ones(ndvi.shape, bool), 4, True)
        self.assertGreater(np.count_nonzero(flags == 3), 0)
        self.assertTrue(np.all(flags[temperature != -9999] != 3))
        self.assertGreater(np.count_nonzero(filled != -9999), np.count_nonzero(temperature != -9999))

    def test_tsharp_rejects_constant_predictor_and_checks_cancellation(self):
        shape = (64, 64)
        original = np.full(shape, 30.0)
        result, flags, report = engine._tsharp_scene(np.full(shape, 0.5), original, np.ones(shape, bool), 4)
        self.assertEqual(report["status"], "skipped")
        np.testing.assert_array_equal(result, original)
        self.assertTrue(np.all(flags == 1))
        feedback = QgsProcessingFeedback()
        feedback.cancel()
        with self.assertRaisesRegex(RuntimeError, "cancelado"):
            engine._tsharp_scene(np.full(shape, 0.5), original, np.ones(shape, bool), 4, feedback=feedback)
        varied = np.linspace(0.1, 0.9, 4096).reshape(shape)
        _, _, report = engine._tsharp_scene(varied, 20 + 20 * varied, np.ones(shape, bool), 4)
        self.assertEqual(report["status"], "skipped")

    def test_fused_product_is_separate_classified_and_auditable(self):
        rows, columns = np.indices((64, 128))
        ndvi = 0.1 + 0.7 * ((rows // 4 * 3 + columns // 4 * 7) % 31) / 30 + 0.02 * (columns % 4) / 3
        choices = np.where(columns < 64, 1, 2)
        red = np.full(ndvi.shape, 12000)
        reflectance = red * 0.0000275 - 0.2
        nir = np.rint((reflectance * (1 + ndvi) / (1 - ndvi) + 0.2) / 0.0000275)
        temperature = 40 - 20 * (1 - (1 - ndvi)**0.625) + (choices - 1) * 10
        thermal = np.rint((temperature + 273.15 - 149) / 0.00341802)
        thermal[20:28, 20:28] = 0
        files = {key: raster(self.directory / (key + '.tif'), array) for key, array in
                 {"red": red, "nir08": nir, "lwir11": thermal, "qa_pixel": np.full(ndvi.shape, 64)}.items()}
        scene_file = raster(self.directory / "CENA_POR_PIXEL.tif", choices)
        manifest = {"source": "landsat89", "coherent_observations": True, "scene_index": scene_file.name,
                    "scenes": [{"index": i, "id": f"SCENE_{i}", "date": f"2024-01-0{i}T12:00:00Z"} for i in (1, 2)],
                    "bands": {key: {"resolution": 30, "file": path.name} for key, path in files.items()}}
        (self.directory / engine.MANIFEST_NAME).write_text(json.dumps(manifest))
        before = {key: hashlib.sha256(path.read_bytes()).digest() for key, path in files.items()}
        for fill in (False, True):
            details = {}
            output = engine.generate_analytic_product("landsat89", self.directory, "lst_fused", "natural",
                        self.directory / f"fused_{fill}.tif", fusion_fill_gaps=fill, fusion_details=details)
            with gdal.Open(output) as dataset:
                self.assertEqual(dataset.GetMetadataItem("PRODUCT_TYPE"), "ESTIMATED_LST")
                self.assertEqual(dataset.GetRasterBand(1).DataType, gdal.GDT_Float32)
                self.assertEqual(dataset.GetMetadataItem("PROVENANCE_MASK"), details["provenance_mask"])
                output_values = dataset.ReadAsArray()
            with gdal.Open(details["original_lst"]) as dataset:
                original = dataset.ReadAsArray()
                np.testing.assert_array_equal(original == -9999, thermal == 0)
            with gdal.Open(details["provenance_mask"]) as dataset:
                codes = dataset.ReadAsArray()
                self.assertEqual(dataset.GetRasterBand(1).DataType, gdal.GDT_Byte)
                if fill:
                    self.assertGreater(np.count_nonzero(codes == 3), 0)
                else:
                    np.testing.assert_array_equal(output_values == -9999, thermal == 0)
            report = json.loads(Path(details["method_report"]).read_text(encoding="utf-8"))
            self.assertEqual(len(report["scenes"]), 2)
            self.assertTrue(all(scene["status"] == "fitted" for scene in report["scenes"]))
            self.assertTrue(Path(output).with_suffix(".qml").is_file())
        for key, path in files.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before[key])
        from imagens_satellite.provider import SateliteCadenteAnalyticProductAlgorithm
        algorithm = SateliteCadenteAnalyticProductAlgorithm()
        algorithm.initAlgorithm()
        result, successful = algorithm.run({"BANDS_FOLDER": str(self.directory), "SOURCE": 3,
                    "PRODUCT": engine.ANALYTIC_PRODUCTS.index("lst_fused"), "FUSION_FILL_GAPS": True,
                    "OUTPUT": str(self.directory / "Processing_Fusion.tif")}, QgsProcessingContext(), QgsProcessingFeedback())
        self.assertTrue(successful)
        self.assertEqual(set(result), {"OUTPUT", "ORIGINAL_LST", "FUSION_MASK", "FUSION_REPORT", "REPORT"})
        self.assertTrue(all(Path(path).is_file() for path in result.values()))
        wanted = self.directory / "preserved.tif"
        wanted.write_bytes(b"old-result")
        with patch.object(engine, "_write_processing_output", side_effect=RuntimeError("publish-failed")):
            with self.assertRaisesRegex(RuntimeError, "publish-failed"):
                engine.generate_analytic_product("landsat89", self.directory, "lst_fused", "natural", wanted)
        self.assertEqual(wanted.read_bytes(), b"old-result")

    def test_parameters_are_grouped_visible_and_helped_for_each_source(self):
        from imagens_satellite.provider import SateliteCadenteAlgorithm, SateliteCadenteAnalyticProductAlgorithm, _parameters_widget, SOURCE_VALUES
        from qgis.core import QgsProcessingParameterDefinition
        algorithm = SateliteCadenteAlgorithm()
        algorithm.initAlgorithm()
        for key in ("SOURCE", "EXTENT", "ZOOM", "YEAR", "MAX_CLOUD", "ESRI_AUTH", "OUTPUT"):
            self.assertFalse(algorithm.parameterDefinition(key).flags() & QgsProcessingParameterDefinition.FlagAdvanced)
        widget = _parameters_widget(algorithm, None)
        try:
            wrappers = widget.mainWidget().wrappers
            context = widget.mainWidget().processing_context
            for position, source in enumerate(SOURCE_VALUES):
                wrappers["SOURCE"].setWidgetValue(position, context)
                analytic = source in engine.MULTIBAND_SOURCES
                expected = {"ZOOM": not analytic, "YEAR": analytic or source == "cloudless", "MAX_CLOUD": analytic,
                            "MONTH_START": analytic, "MONTH_END": analytic,
                            "DOWNLOAD_WORKERS": analytic, "ESRI_AUTH": source == "esri"}
                for key, visible in expected.items():
                    self.assertEqual(not wrappers[key].wrappedWidget().isHidden(), visible, (source, key))
            for wrapper in wrappers.values():
                self.assertTrue(wrapper.wrappedWidget().toolTip())
        finally:
            widget.close()
            widget.deleteLater()
        algorithm = SateliteCadenteAnalyticProductAlgorithm()
        algorithm.initAlgorithm()
        widget = _parameters_widget(algorithm, None)
        try:
            wrappers = widget.mainWidget().wrappers
            context = widget.mainWidget().processing_context
            (self.directory / engine.MANIFEST_NAME).write_text(json.dumps({"source": "landsat89"}))
            wrappers["BANDS_FOLDER"].setWidgetValue(str(self.directory), context)
            self.assertEqual(wrappers["SOURCE"].widgetValue(), 3)
            wrappers["PRODUCT"].setWidgetValue(engine.ANALYTIC_PRODUCTS.index("lst_fused"), context)
            self.assertTrue(wrappers["SAVI_L"].wrappedWidget().isHidden())
            wrappers["PRODUCT"].setWidgetValue(engine.ANALYTIC_PRODUCTS.index("savi"), context)
            self.assertFalse(wrappers["SAVI_L"].wrappedWidget().isHidden())
            self.assertEqual(wrappers["SAVI_L"].widgetValue(), 0.5)
            wrappers["PRODUCT"].setWidgetValue(engine.ANALYTIC_PRODUCTS.index("lst_fused"), context)
            self.assertFalse(wrappers["FUSION_FILL_GAPS"].wrappedWidget().isHidden())
            self.assertFalse(wrappers["FUSION_MIN_R2"].wrappedWidget().isHidden())
            self.assertTrue(wrappers["COMPOSITION"].wrappedWidget().isHidden())
            self.assertFalse(wrappers["FUSION_FILL_GAPS"].widgetValue())
            wrappers["SOURCE"].setWidgetValue(0, context)
            self.assertEqual(wrappers["PRODUCT"].widgetValue(), 0)
            self.assertTrue(wrappers["FUSION_FILL_GAPS"].wrappedWidget().isHidden())
            self.assertTrue(wrappers["PRODUCT"].wrappedWidget().view().isRowHidden(engine.ANALYTIC_PRODUCTS.index("lst_fused")))
            for parameter in algorithm.parameterDefinitions():
                self.assertTrue(parameter.help())
                self.assertNotEqual(parameter.description(), parameter.name())
        finally:
            widget.close()
            widget.deleteLater()

    def test_classified_postprocessor_restores_false_color_without_changing_pixels(self):
        from imagens_satellite.provider import ClassifiedPostProcessor, _register_output
        from qgis.core import QgsRasterLayer, QgsSingleBandGrayRenderer, QgsProcessingContext
        source = raster(self.directory / "index.tif", np.linspace(-1, 1, 4096).reshape(64, 64),
                        dtype=gdal.GDT_Float32, nodata=-9999)
        layer = QgsRasterLayer(str(source), "NDVI")
        before = hashlib.sha256(source.read_bytes()).digest()
        engine._classify_product(source, "ndvi")
        layer.setRenderer(QgsSingleBandGrayRenderer(layer.dataProvider(), 1))
        context = QgsProcessingContext()
        feedback = QgsProcessingFeedback()
        try:
            context.addLayerToLoadOnCompletion(str(source), QgsProcessingContext.LayerDetails("NDVI", QgsProject(), "OUTPUT"))
            _register_output(context, source, source, ClassifiedPostProcessor())
            context.layerToLoadOnCompletionDetails(str(source)).postProcessor().postProcessLayer(layer, context, feedback)
            self.assertEqual(layer.renderer().type(), "singlebandpseudocolor", feedback.textLog())
            self.assertEqual(layer.renderer().shader().rasterShaderFunction().colorRampType(), engine.QgsColorRampShader.Discrete)
            self.assertEqual(len(layer.renderer().shader().rasterShaderFunction().colorRampItemList()), 5)
            self.assertIsNotNone(layer.renderer().shader().rasterShaderFunction().sourceColorRamp())
            self.assertTrue(layer.customProperty("satelite/classification"))
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
            from qgis.gui import QgsSingleBandPseudoColorRendererWidget
            from qgis.PyQt.QtWidgets import QTreeWidget
            from qgis.PyQt import sip
            widget = QgsSingleBandPseudoColorRendererWidget(layer, layer.extent())
            try:
                self.assertTrue(any(tree.topLevelItemCount() == 5 for tree in widget.findChildren(QTreeWidget)))
            finally:
                sip.delete(widget)
            source.with_suffix(".qml").unlink()
            ClassifiedPostProcessor().postProcessLayer(layer, context, feedback)
            self.assertIn("Estilo classificado nao carregado", feedback.textLog())
        finally:
            del layer
            del context

    def test_postprocessors_apply_contrast_only_when_requested(self):
        from imagens_satellite.provider import RGBPostProcessor, BandsPostProcessor
        layer = object()
        context = QgsProcessingContext()
        with patch("imagens_satellite.provider.apply_rgb_preview_style"), patch("imagens_satellite.provider._apply_layer_metadata"), patch("imagens_satellite.provider._apply_stretch") as stretch:
            RGBPostProcessor().postProcessLayer(layer, context, QgsProcessingFeedback())
            BandsPostProcessor(self.directory, False).postProcessLayer(layer, context, QgsProcessingFeedback())
            stretch.assert_not_called()
            BandsPostProcessor(self.directory, True).postProcessLayer(layer, context, QgsProcessingFeedback())
            stretch.assert_called_once_with(layer)
            stretch.reset_mock()
        del layer

    def test_native_interface_changes_source_without_custom_widget_class(self):
        from processing.core.Processing import Processing
        from imagens_satellite.provider import SateliteCadenteAlgorithm, _parameters_widget
        Processing.initialize()
        algorithm = SateliteCadenteAlgorithm()
        algorithm.initAlgorithm()
        widget = _parameters_widget(algorithm, None)
        try:
            wrappers = widget.mainWidget().wrappers
            self.assertTrue(wrappers["ZOOM"].wrappedWidget().view().isRowHidden(1))
            wrappers["SOURCE"].setWidgetValue(7, widget.mainWidget().processing_context)
            self.assertTrue(wrappers["ZOOM"].wrappedWidget().isHidden())
            self.assertFalse(wrappers["YEAR"].wrappedWidget().isHidden())
            self.assertTrue(wrappers["ESRI_AUTH"].wrappedWidget().isHidden())
        finally:
            widget.close()
            widget.deleteLater()

    def test_retry_refreshes_expired_token(self):
        with patch.object(gdal, "Warp", side_effect=[RuntimeError("HTTP response code: 403"), object()]) as warp:
            engine._warp_remote_with_retry(self.directory / "unused.tif", "expired", None, "test", refresh_source=lambda: "renewed")
            self.assertEqual(warp.call_args.args[1], "renewed")

    def test_stac_paging_preserves_filters(self):
        request = engine.Request("https://catalog.example/search", data=json.dumps({"collections": ["landsat"], "bbox": [0, 0, 1, 1]}).encode(), method="POST")
        pages = [
            {"features": [{"id": "first"}], "links": [{"rel": "next", "href": "/search", "method": "POST", "merge": True, "body": {"token": "next"}}]},
            {"features": [{"id": "second"}]},
        ]
        with patch.object(engine, "urlopen", side_effect=[io.BytesIO(json.dumps(page).encode()) for page in pages]) as fetch:
            self.assertEqual([item["id"] for item in engine._stac_features(request, None)], ["first", "second"])
            body = json.loads(fetch.call_args.args[0].data)
            self.assertEqual(body["collections"], ["landsat"])
            self.assertEqual(body["token"], "next")

    def test_provider_runs_engine_and_registers_alternative_output(self):
        from imagens_satellite import provider
        scenes = self.scenes()
        project = QgsProject()
        context = QgsProcessingContext()
        context.setProject(project)
        wanted = self.directory / "OUTPUT.tif"
        wanted.write_bytes(b"previous-result")
        context.addLayerToLoadOnCompletion(str(wanted), QgsProcessingContext.LayerDetails("image", project, "OUTPUT"))
        algorithm = provider.SateliteCadenteAlgorithm()
        algorithm.initAlgorithm()
        parameters = {"SOURCE": 7, "EXTENT": "500000,500120,8699880,8700000 [EPSG:32724]", "YEAR": 2024,
                      "MAX_CLOUD": 20, "ZOOM": 0, "STRETCH_CONTRAST": True, "OUTPUT": str(wanted)}
        with patch.object(provider, "run", side_effect=engine.run), patch.object(engine, "_search_multiband", return_value=(scenes, False, 2024)), patch.object(engine, "_planetary_computer_token", return_value="mock"), patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href):
            result = algorithm.processAlgorithm(parameters, context, QgsProcessingFeedback())["OUTPUT"]
        self.assertNotEqual(result, str(wanted))
        self.assertEqual(wanted.read_bytes(), b"previous-result")
        self.assertEqual(tuple(context.layersToLoadOnCompletion()), (result,))
        self.assertEqual(len(project.mapLayers()), 0)

    def test_rgb_render_survives_zoom_in_out_and_project_reprojection(self):
        values = np.arange(4096, dtype=np.uint16).reshape(64, 64) + 10000
        bands = {key: (30, raster(self.directory / f"{key}.tif", values, 0.0000275, -0.2)) for key in ("red", "green", "blue")}
        preview = engine._create_rgb_preview("landsat89", bands, self.directory)
        output = engine._write_processing_output(preview, self.directory / "rgb.tif")
        with gdal.Open(str(output)) as dataset:
            self.assertIn(dataset.GetRasterBand(1).GetScale(), (None, 1.0))
        with gdal.Open(str(bands["red"][1])) as dataset:
            self.assertEqual(dataset.GetRasterBand(1).GetScale(), 0.0000275)
        self.assertEqual(check_render_at_scales(str(output)), 3)

    def test_classification_methods_preserve_values_and_reload_discrete_legend(self):
        from qgis.core import QgsRasterLayer
        values = np.linspace(-0.5, 0.9, 4096, dtype=np.float32).reshape(64, 64)
        values[0, 0] = -9999
        for method in engine.CLASSIFICATION_METHODS:
            with self.subTest(method=method):
                source = raster(self.directory / f"{method}.tif", values, dtype=gdal.GDT_Float32, nodata=-9999)
                digest = hashlib.sha256(source.read_bytes()).digest()
                engine._classify_product(source, "ndvi", method, 5, "0;0,2;0,4;0,6")
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)
                layer = QgsRasterLayer(str(source), "NDVI")
                self.assertEqual(layer.renderer().type(), "singlebandpseudocolor")
                shader = layer.renderer().shader().rasterShaderFunction()
                self.assertEqual(shader.colorRampType(), shader.Discrete)
                self.assertFalse(shader.legendSettings().useContinuousLegend())
                info = json.loads(layer.customProperty("satelite/classification"))
                self.assertEqual(info["method"], method)
                self.assertLessEqual(info["sample_size"], 10000)
                self.assertGreaterEqual(len(shader.colorRampItemList()), 2)
                self.assertTrue(shader.shade(-100)[0])
                self.assertTrue(shader.shade(100)[0])
                block = layer.renderer().block(1, layer.extent(), 64, 64)
                self.assertEqual(QColor.fromRgba(block.color(0, 0)).alpha(), 0)
                self.assertGreater(QColor.fromRgba(block.color(63, 63)).alpha(), 0)
                del block, layer
                self.assertEqual(check_render_at_scales(str(source), classified=True), 3)

    def test_classification_manual_boundaries_and_constant_raster(self):
        from qgis.core import QgsRasterLayer
        path = raster(self.directory / "constant.tif", [[0.3, 0.3]], dtype=gdal.GDT_Float32, nodata=-9999)
        engine._classify_product(path, "savi", "quantile")
        layer = QgsRasterLayer(str(path), "SAVI")
        self.assertEqual(len(layer.renderer().shader().rasterShaderFunction().colorRampItemList()), 1)
        del layer
        engine._classify_product(path, "savi", "manual", manual_limits="0;0,3;0,6")
        layer = QgsRasterLayer(str(path), "SAVI")
        shader = layer.renderer().shader().rasterShaderFunction()
        self.assertEqual(shader.shade(0.3)[1:5], shader.colorRampItemList()[1].color.getRgb())
        self.assertNotEqual(shader.shade(0.31)[1:5], shader.shade(0.3)[1:5])
        del layer

    def test_automatic_labels_include_product_relative_rank_and_exact_limits(self):
        from qgis.core import QgsRasterLayer
        for product in ('ndvi', 'savi', 'ndwi', 'ndbi', 'lst', 'lst_fused'):
            path = raster(self.directory / (product + '.tif'), np.linspace(-0.5,0.9,256).reshape(16,16),
                          dtype=gdal.GDT_Float32,nodata=-9999)
            before = hashlib.sha256(path.read_bytes()).digest()
            engine._classify_product(path,product,'equal',5)
            layer = QgsRasterLayer(str(path),product)
            items = layer.renderer().shader().rasterShaderFunction().colorRampItemList()
            self.assertEqual(len(items),5)
            self.assertIn('muito baixo',items[0].label)
            self.assertIn('muito alto',items[-1].label)
            self.assertIn('relativo',items[0].label)
            self.assertIn('<=',items[0].label)
            self.assertIn(',',items[0].label)
            if product in ('lst','lst_fused'):
                self.assertIn('Celsius',items[0].label)
            else:
                self.assertIn(product.upper(),items[0].label)
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),before)
            del layer

    def test_invalid_classification_and_all_nodata_do_not_publish_output(self):
        path = raster(self.directory / "source.tif", [[-9999]], dtype=gdal.GDT_Float32, nodata=-9999)
        output = self.directory / "never.tif"
        for limits in ("", "0;0", "0,6;0,2", "nan;0,2", "inf", "0;abc"):
            with self.subTest(limits=limits), self.assertRaises(RuntimeError):
                engine._write_processing_output(path, output, True, "ndvi", classification="manual", manual_limits=limits)
        with self.assertRaisesRegex(RuntimeError, "pixels validos"):
            engine._write_processing_output(path, output, True, "ndvi")
        valid = raster(self.directory / "valid.tif", [[0.1, 0.8]], dtype=gdal.GDT_Float32, nodata=-9999)
        from qgis.core import QgsRasterLayer
        with patch.object(QgsRasterLayer, "saveNamedStyle", side_effect=RuntimeError("style failed")):
            with self.assertRaisesRegex(RuntimeError, "style failed"):
                engine._write_processing_output(valid, output, True, "ndvi")
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix(".qml").exists())
        self.assertFalse(list(self.directory.glob("qgis_output_*")))

    def test_classified_writer_keeps_style_at_actual_collision_path(self):
        from qgis.core import QgsRasterLayer
        source = raster(self.directory / "source.tif", [[0.1, 0.8]], dtype=gdal.GDT_Float32, nodata=-9999)
        wanted = self.directory / "output.tif"
        wanted.write_bytes(b"previous")
        actual = engine._write_processing_output(source, wanted, True, "ndvi")
        self.assertNotEqual(actual, wanted)
        self.assertEqual(wanted.read_bytes(), b"previous")
        self.assertTrue(actual.with_suffix(".qml").is_file())
        layer = QgsRasterLayer(str(actual), "NDVI")
        self.assertEqual(layer.renderer().type(), "singlebandpseudocolor")
        del layer
        with gdal.Open(str(actual)) as dataset:
            np.testing.assert_array_equal(dataset.ReadAsArray(), [[np.float32(0.1), np.float32(0.8)]])

    def test_classification_widgets_follow_product_and_method(self):
        from imagens_satellite.provider import SateliteCadenteAnalyticProductAlgorithm, _parameters_widget
        algorithm = SateliteCadenteAnalyticProductAlgorithm()
        algorithm.initAlgorithm()
        widget = _parameters_widget(algorithm, None)
        try:
            wrappers = widget.mainWidget().wrappers
            context = widget.mainWidget().processing_context
            self.assertTrue(wrappers["CLASSIFICATION"].wrappedWidget().isHidden())
            wrappers["PRODUCT"].setWidgetValue(1, context)
            self.assertFalse(wrappers["CLASSIFICATION"].wrappedWidget().isHidden())
            self.assertFalse(wrappers["CLASS_COUNT"].wrappedWidget().isHidden())
            self.assertTrue(wrappers["CLASS_LIMITS"].wrappedWidget().isHidden())
            wrappers["CLASSIFICATION"].setWidgetValue(4, context)
            self.assertTrue(wrappers["CLASS_COUNT"].wrappedWidget().isHidden())
            self.assertFalse(wrappers["CLASS_LIMITS"].wrappedWidget().isHidden())
            wrappers["PRODUCT"].setWidgetValue(5, context)
            self.assertTrue(wrappers["CLASSIFICATION"].wrappedWidget().isHidden())
            self.assertTrue(wrappers["CLASS_LIMITS"].wrappedWidget().isHidden())
        finally:
            widget.close()
            widget.deleteLater()

    def test_analytic_provider_saves_classification_without_opening_output(self):
        from imagens_satellite.provider import SateliteCadenteAnalyticProductAlgorithm
        red = raster(self.directory / "red.tif", [[10000, 10000]], 0.0000275, -0.2)
        nir = raster(self.directory / "nir.tif", [[10000, 14000]], 0.0000275, -0.2)
        manifest = {"source": "landsat89", "coherent_observations": True,
                    "bands": {"red": {"resolution": 30, "file": red.name},
                              "nir08": {"resolution": 30, "file": nir.name}}}
        (self.directory / engine.MANIFEST_NAME).write_text(json.dumps(manifest))
        context = QgsProcessingContext()
        parameters = {
            "BANDS_FOLDER": str(self.directory), "SOURCE": 3, "PRODUCT": 1,
            "CLASSIFICATION": 4, "CLASS_LIMITS": "0;0,2;0,4;0,6",
            "OUTPUT": str(self.directory / "ndvi.tif"),
        }
        def execute_in_worker():
            worker_algorithm = SateliteCadenteAnalyticProductAlgorithm()
            worker_algorithm.initAlgorithm()
            return worker_algorithm.processAlgorithm(parameters, QgsProcessingContext(), QgsProcessingFeedback())["OUTPUT"]
        with ThreadPoolExecutor(max_workers=1) as worker:
            result = worker.submit(execute_in_worker).result()
        self.assertEqual(context.layersToLoadOnCompletion(), {})
        from qgis.core import QgsRasterLayer
        layer = QgsRasterLayer(result, "NDVI")
        info = json.loads(layer.customProperty("satelite/classification"))
        self.assertEqual(info["limits"], [0, 0.2, 0.4, 0.6])
        self.assertEqual(info["actual_classes"], 5)
        del layer
        from qgis.core import QgsProcessingOutputLayerDefinition
        from imagens_satellite.provider import ClassifiedPostProcessor
        project = QgsProject()
        context.setProject(project)
        parameters["OUTPUT"] = QgsProcessingOutputLayerDefinition(str(self.directory / "opened.tif"), project)
        algorithm = SateliteCadenteAnalyticProductAlgorithm()
        algorithm.initAlgorithm()
        result, successful = algorithm.run(parameters, context, QgsProcessingFeedback())
        self.assertTrue(successful)
        self.assertIsInstance(context.layerToLoadOnCompletionDetails(result["OUTPUT"]).postProcessor(), ClassifiedPostProcessor)

    def test_parallel_download_preserves_scene_choices_and_band_values(self):
        scenes = self.scenes()
        original = engine._warp_asset
        arrays = []
        peaks = []
        for workers in (1, 3):
            folder = self.directory / f"workers_{workers}"
            folder.mkdir()
            active = peak = 0
            lock = threading.Lock()
            def delayed(*args, **kwargs):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                try:
                    time.sleep(0.02)
                    return original(*args, **kwargs)
                finally:
                    with lock:
                        active -= 1
            with patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href), patch.object(engine, "_warp_asset", side_effect=delayed):
                observations, choice, coverage = engine._prepare_observations(
                    scenes, "landsat89", (500000, 8699880, 500120, 8700000), "EPSG:32724", 30,
                    folder, {}, None, download_workers=workers,
                )
            self.assertEqual(coverage, 1)
            self.assertEqual([item["index"] for item in observations], [1, 2])
            peaks.append(peak)
            with gdal.Open(str(choice)) as dataset:
                result = [dataset.ReadAsArray()]
            for key in ("red", "green", "blue", "nir08", "swir16", "swir22"):
                with patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href):
                    path, _ = engine._compose_observation_band(observations, choice, "landsat89", key, 30,
                                                              (500000, 8699880, 500120, 8700000), "EPSG:32724", folder, {}, None)
                with gdal.Open(str(path)) as dataset:
                    result.append(dataset.ReadAsArray())
            arrays.append(result)
        self.assertEqual(peaks[0], 1)
        self.assertGreater(peaks[1], 1)
        self.assertLessEqual(peaks[1], 3)
        for serial, parallel in zip(*arrays):
            np.testing.assert_array_equal(serial, parallel)

    def test_parallel_retry_renews_shared_token_once(self):
        state = {"token": "expired", "collection": "landsat"}
        barrier = threading.Barrier(3)
        def fail_then_renew(destination, source, options, label, feedback, refresh):
            barrier.wait(timeout=5)
            refresh()
            return None
        item = {"id": "scene", "assets": {key: {"href": key} for key in ("red", "green", "blue")}}
        with patch.object(engine, "_warp_remote_with_retry", side_effect=fail_then_renew), patch.object(engine, "_asset_path", side_effect=lambda href, token=None: href), patch.object(engine, "_planetary_computer_token", return_value="new") as refresh:
            with ThreadPoolExecutor(max_workers=3) as executor:
                list(executor.map(lambda key: engine._warp_asset(item, key, (0, 0, 30, 30), "EPSG:32724", 30,
                                                                self.directory, state, None), item["assets"]))
        refresh.assert_called_once()
        self.assertEqual(state["token"], "new")


def live_smoke(source, download_workers=engine.ANALYTIC_DOWNLOAD_WORKERS, month_start=1, month_end=12):
    """Recorte pequeno descartavel: exercita catalogo real, download e produtos."""
    project = QgsProject()
    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), QgsCoordinateReferenceSystem("EPSG:3857"), project)
    area = transform.transformBoundingBox(QgsRectangle(-38.986, -12.225, -38.980, -12.220))
    bounds = (area.xMinimum(), area.yMinimum(), area.xMaximum(), area.yMaximum())
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="satelite_live_") as temporary:
        folder = Path(temporary)
        feedback = QgsProcessingFeedback()
        year = {"landsat7": 2002, "landsat45": 2011}.get(source, 2024)
        details = {}
        with gdal.config_options(engine.NETWORK_OPTIONS):
            result = engine._run_multiband(project, bounds, "teste descartavel", source, year, 20, feedback,
                                           folder / "bands", False, None, folder / "OUTPUT.tif", details,
                                           download_workers=download_workers, month_start=month_start, month_end=month_end)
        manifest = json.loads((Path(details["bands_folder"]) / engine.MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["search_months"] == [month_start, month_end]
        assert all(month_start <= int(scene["date"][5:7]) <= month_end for scene in manifest["scenes"])
        with gdal.Open(result) as dataset:
            assert dataset.RasterCount == 3 and dataset.GetSpatialRef().IsProjected()
            assert np.count_nonzero(dataset.ReadAsArray()) > 0
        product = engine.generate_analytic_product(source, details["bands_folder"], "ndvi", "natural", folder / "NDVI.tif", feedback)
        rendered_scales = check_render_at_scales(result)
        with gdal.Open(product) as dataset:
            values = dataset.ReadAsArray()
            valid = values[values != -9999]
            assert valid.size and np.isfinite(valid).all()
            assert valid.min() >= -1.000001 and valid.max() <= 1.000001
            product_hash = hashlib.sha256(values.tobytes()).hexdigest()
        band_hashes = {}
        for key, value in manifest["bands"].items():
            with gdal.Open(str(Path(details["bands_folder"]) / value["file"])) as dataset:
                band_hashes[key] = hashlib.sha256(dataset.ReadAsArray().tobytes()).hexdigest()
        print(json.dumps({"source": source, "bands": len(manifest["bands"]), "coverage": manifest["optical_valid_fraction"],
                          "crs": manifest["crs"], "ndvi_min": float(valid.min()), "ndvi_max": float(valid.max()),
                          "project_layers": len(project.mapLayers()), "rendered_scales": rendered_scales,
                          "download_workers": download_workers, "elapsed_seconds": round(time.perf_counter() - started, 3),
                          "search_months": manifest["search_months"], "scene_dates": [scene["date"] for scene in manifest["scenes"]],
                          "band_hashes": band_hashes, "ndvi_sha256": product_hash}, indent=2))
        print(feedback.textLog())


def local_products(bands_directory, output_directory, boundary, feedback=None):
    """Teste persistente dos produtos Processing; chamar em tarefa QGIS, nao no projeto principal."""
    import processing
    from qgis.core import QgsRasterLayer
    from imagens_satellite import satellite_mosaic_qgis as plugin_engine
    bands = Path(bands_directory)
    folder = Path(output_directory)
    folder.mkdir(parents=True, exist_ok=False)
    feedback = feedback or QgsProcessingFeedback()
    manifest = json.loads((bands / engine.MANIFEST_NAME).read_text(encoding='utf-8'))
    assert manifest['source'] == 'landsat89', 'Este teste local exige bandas Landsat 8-9'
    inputs = [bands / entry['file'] for entry in manifest['bands'].values()]
    inputs.append(bands / manifest['scene_index'])
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    arrays, raws = {}, {}
    for key in ('red', 'nir08', 'green', 'swir16', 'lwir11'):
        with gdal.Open(str(bands / manifest['bands'][key]['file'])) as ds:
            raw = ds.ReadAsArray()
            raws[key] = raw
            band = ds.GetRasterBand(1)
            arrays[key] = raw.astype(np.float32) * band.GetScale() + band.GetOffset()
    target = QgsCoordinateReferenceSystem('EPSG:31984')
    mask_path = processing.run('native:reprojectlayer', {'INPUT': boundary, 'TARGET_CRS': target,
        'OUTPUT': str(folder / 'limite_sirgas2000.gpkg')}, feedback=feedback)['OUTPUT']
    jobs = [(key, key, 'natural') for key in ('savi', 'ndwi', 'ndbi', 'ndvi', 'lst', 'lst_fused', 'rgb')]
    jobs += [('rgb_' + composition, 'composition', composition) for composition in ('natural', 'vegetation', 'urban')]
    report = {'bands': str(bands), 'scenes': manifest['scenes'], 'version': plugin_engine.VERSION,
              'boundary': boundary, 'savi_l': 0.5, 'fill_gaps': False, 'products': []}
    for name, product, composition in jobs:
        started = time.perf_counter()
        entry = {'name': name, 'product': product, 'composition': composition}
        report['products'].append(entry)
        feedback.setProgressText('Testando produto: ' + name)
        try:
            outputs = processing.run('imagens_satellite:produto_analitico', {
                'BANDS_FOLDER': str(bands), 'SOURCE': 3,
                'PRODUCT': plugin_engine.ANALYTIC_PRODUCTS.index(product),
                'COMPOSITION': tuple(plugin_engine.RGB_COMPOSITIONS).index(composition),
                'SAVI_L': 0.5, 'CLASSIFICATION': 2, 'CLASS_COUNT': 5,
                'FUSION_FILL_GAPS': False, 'FUSION_MIN_R2': 0.2,
                'OUTPUT': str(folder / (name + '_complemento.tif'))}, feedback=feedback)
            source = Path(outputs['OUTPUT'])
            scalar = product not in ('rgb', 'composition')
            with gdal.Open(str(source)) as ds:
                values = ds.ReadAsArray()
                if product in ('ndvi', 'ndwi', 'ndbi', 'savi', 'lst'):
                    if product == 'lst':
                        good = raws['lwir11'] != 0
                        expected = arrays['lwir11'] - 273.15
                    else:
                        first, second = {'ndvi': ('nir08', 'red'), 'savi': ('nir08', 'red'),
                                         'ndwi': ('green', 'nir08'), 'ndbi': ('swir16', 'nir08')}[product]
                        a, b = arrays[first], arrays[second]
                        denominator = a + b + (0.5 if product == 'savi' else 0)
                        good = (raws[first] != 0) & (raws[second] != 0) & (a >= 0) & (b >= 0) & (abs(denominator) > 1e-8)
                        with np.errstate(divide='ignore', invalid='ignore'):
                            expected = (1.5 if product == 'savi' else 1) * (a - b) / denominator
                    assert np.array_equal(values != -9999, good), 'Mascara do produto diverge das bandas'
                    np.testing.assert_allclose(values[good], expected[good], atol=1e-6, rtol=1e-6)
                    entry['formula_verified'] = True
                if product in ('rgb', 'composition'):
                    keys = ('red', 'green', 'blue') if product == 'rgb' else plugin_engine._composition_assets('landsat89', composition)
                    assert ds.RasterCount == 3
                    for position, key in enumerate(keys):
                        with gdal.Open(str(bands / manifest['bands'][key]['file'])) as band_ds:
                            np.testing.assert_array_equal(values[position], band_ds.ReadAsArray())
                    entry['rgb_bands_verified'] = True
                if product == 'lst_fused':
                    with gdal.Open(outputs['ORIGINAL_LST']) as original:
                        assert np.array_equal(values == -9999, original.ReadAsArray() == -9999)
                    with gdal.Open(outputs['FUSION_MASK']) as flags:
                        codes = flags.ReadAsArray()
                        assert not np.any(codes == 3), 'Lacunas preenchidas sem autorizacao'
                        entry['estimated_pixels'] = int(np.count_nonzero(codes == 2))
                    entry['fusion_report'] = json.loads(Path(outputs['FUSION_REPORT']).read_text(encoding='utf-8'))
                transform = ds.GetGeoTransform()
                bounds = plugin_engine._raster_bounds(source)
                width, height = ds.RasterXSize, ds.RasterYSize
                src_crs = osr.SpatialReference(wkt=ds.GetProjection())
                dst_crs = osr.SpatialReference()
                dst_crs.ImportFromEPSG(31984)
                area = osr.CoordinateTransformation(src_crs, dst_crs).TransformBounds(*bounds, 21)
            final = folder / (name + '_municipio_SIRGAS2000.tif')
            with gdal.Warp(str(final), str(source), dstSRS=target.authid(),
                          outputBounds=area,
                          width=width, height=height, resampleAlg='near', overviewLevel='NONE',
                          srcNodata=-9999 if scalar else 0, dstNodata=-9999 if scalar else 0,
                          creationOptions=['TILED=YES', 'COMPRESS=DEFLATE', 'PREDICTOR=3' if scalar else 'PREDICTOR=2']) as ds:
                np.testing.assert_array_equal(ds.ReadAsArray(), values)
                mask = gdal.GetDriverByName('MEM').Create('', width, height, 1, gdal.GDT_Byte)
                mask.SetGeoTransform(ds.GetGeoTransform())
                mask.SetProjection(ds.GetProjection())
                gdal.Rasterize(mask, mask_path, options=gdal.RasterizeOptions(burnValues=[1]))
                inside = mask.ReadAsArray() == 1
                if scalar:
                    values[~inside] = -9999
                    ds.GetRasterBand(1).WriteArray(values)
                    valid = values != -9999
                    assert valid.any() and np.isfinite(values[valid]).all()
                    entry.update(min=float(values[valid].min()), max=float(values[valid].max()),
                                 municipal_coverage=float(valid.sum() / inside.sum()))
                else:
                    for position in range(3):
                        values[position][~inside] = 0
                        ds.GetRasterBand(position + 1).WriteArray(values[position])
            layer = QgsRasterLayer(str(final), name)
            if scalar:
                plugin_engine._classify_product(final, product, 'jenks', 5, feedback=feedback)
                layer.loadNamedStyle(str(final.with_suffix('.qml')))
                entry['classes'] = len(layer.renderer().shader().rasterShaderFunction().colorRampItemList())
                assert entry['classes'] == 5
            else:
                plugin_engine.apply_rgb_preview_style(layer)
                layer.saveNamedStyle(str(final.with_suffix('.qml')))
            settings = QgsMapSettings()
            settings.setLayers([layer])
            settings.setDestinationCrs(target)
            settings.setOutputSize(QSize(400, 350))
            settings.setBackgroundColor(QColor('white'))
            for factor in (1, 4, 0.75):
                extent = QgsRectangle(layer.extent())
                extent.scale(factor)
                settings.setExtent(extent)
                job = QgsMapRendererSequentialJob(settings)
                job.start()
                job.waitForFinished()
                screenshot = job.renderedImage()
                colors = {screenshot.pixelColor(x, y).rgba() for x in range(0, 400, 4) for y in range(0, 350, 4)}
                assert len(colors) > 1 and not job.errors(), 'Renderizacao vazia ou com erro'
            entry.update(status='ok', original=str(source), final=str(final), outputs=outputs, rendered_scales=3)
        except Exception as exc:
            import traceback
            entry.update(status='failed', error=str(exc), traceback=traceback.format_exc())
        entry['seconds'] = round(time.perf_counter() - started, 3)
        (folder / 'TESTE_PRODUTOS.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value for path, value in hashes.items()), 'Bandas originais alteradas'
    report['inputs_unchanged'] = True
    report['scope'] = 'Cena Landsat 8 de 07/12/2015; nao valida todos os sensores/fontes/parametros'
    (folder / 'TESTE_PRODUTOS.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    (folder / 'PROCESSAMENTO.log').write_text(feedback.textLog(), encoding='utf-8')
    return report


def review_products(report_path):
    """Audita QA e gera classes numericas, preservando os produtos continuos."""
    import csv
    from datetime import datetime
    from qgis.core import (QgsRasterLayer, QgsColorRampShader, QgsRasterShader,
                           QgsSingleBandPseudoColorRenderer)
    report_path = Path(report_path)
    previous = json.loads(report_path.read_text(encoding='utf-8'))
    folder = report_path.parent / ('revisao_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    folder.mkdir(exist_ok=False)
    bands = Path(previous['bands'])
    manifest = json.loads((bands / engine.MANIFEST_NAME).read_text(encoding='utf-8'))
    qa_path = bands / manifest['bands']['qa_pixel']['file']
    report = {'scene': previous['scenes'], 'products': [], 'limitations': [
        'Sem ST_QA, ST_CDIST e QA_RADSAT: revisao termica e de saturacao incompleta.',
        'QA_PIXEL conservada apos mosaico: nao recupera todos os pixels rejeitados da cena original.',
        'Classes numericas nao comprovam uso do solo, agua ou ilha de calor.',
        'Fusao termica e estimativa; grades de 30 m nao sao observacoes termicas a 30 m.',
        'Limite municipal derivado do OpenStreetMap, nao oficial IBGE.'],
        'references': ['Aplicacaoindices.pdf, p. 4 e 6: SAVI e L=0,5.',
            'submissao-2752-arquivo-11261-1.pdf, p. 2-3: mesma data, bandas 4/5 e classes termicas de 1 Celsius.',
            'USGS Collection 2 QA bands: https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands',
            'USGS Surface Temperature: https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature',
            'McFeeters (1996), DOI 10.1080/01431169608948714; Zha et al. (2003), DOI 10.1080/01431160304987: equacoes, nao limiares locais.']}
    for entry in previous['products']:
        if entry['status'] != 'ok' or entry['product'] in ('rgb', 'composition'):
            continue
        path = Path(entry['final'])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with gdal.Open(str(path)) as ds:
            values = ds.ReadAsArray()
            valid = np.isfinite(values) & (values != ds.GetRasterBand(1).GetNoDataValue())
            gt = ds.GetGeoTransform()
            bounds = (gt[0], gt[3] + gt[5]*ds.RasterYSize, gt[0]+gt[1]*ds.RasterXSize, gt[3])
            with gdal.Warp('', str(qa_path), format='MEM', dstSRS=ds.GetProjection(),
                           outputBounds=bounds, width=ds.RasterXSize, height=ds.RasterYSize,
                           resampleAlg='near') as qa_ds:
                qa = qa_ds.ReadAsArray().astype(np.uint16)
            bad = (qa == 0) | ((qa & 63) != 0)
            assert not np.any(valid & bad), 'Produto contem pixels QA invalidos; corrigir antes de classificar'
            layer = QgsRasterLayer(str(path), entry['name'])
            layer.loadNamedStyle(str(path.with_suffix('.qml')))
            shader = layer.renderer().shader().rasterShaderFunction()
            thermal = entry['product'] in ('lst', 'lst_fused')
            if thermal:
                low, high = np.floor(values[valid].min()), np.ceil(values[valid].max())
                cuts = np.arange(low + 1, high + 1, dtype=float)
                colors = [QColor.fromHsvF(0.66*(1-i/max(1,len(cuts)-1)), 0.85, 0.95) for i in range(len(cuts))]
                labels = [f'{int(c-1)} < T <= {int(c)} Celsius' for c in cuts]
                labels[0] = f'{int(low)} <= T <= {int(cuts[0])} Celsius'
                shader.setColorRampItemList([QgsColorRampShader.ColorRampItem(c,color,label)
                    for c,color,label in zip(cuts,colors,labels)])
                method = 'Intervalos fixos de 1 Celsius; referencia local p. 3'
            else:
                items = shader.colorRampItemList()
                cuts = np.array([item.value for item in items])
                levels = ['muito baixo', 'baixo', 'medio', 'alto', 'muito alto']
                labels = []
                for i,item in enumerate(items):
                    interval = f'<= {cuts[i]:.6f}' if i == 0 else (f'> {cuts[i-1]:.6f}' if not np.isfinite(cuts[i]) else f'> {cuts[i-1]:.6f} e <= {cuts[i]:.6f}')
                    item.label = f'{entry["name"].upper()} relativo {levels[i]} ({interval})'
                    labels.append(item.label)
                shader.setColorRampItemList(items)
                method = 'Jenks, 5 classes relativas desta cena; nao limiares universais'
            classified = np.zeros(values.shape, dtype=np.uint8)
            classified[valid] = np.searchsorted(cuts, values[valid], side='left') + 1
            assert classified[valid].min() >= 1 and classified[valid].max() <= len(cuts)
            output = folder / (entry['name'] + '_classes.tif')
            out = gdal.GetDriverByName('GTiff').Create(str(output), ds.RasterXSize, ds.RasterYSize, 1,
                gdal.GDT_Byte, options=['TILED=YES','COMPRESS=DEFLATE'])
            out.SetProjection(ds.GetProjection()); out.SetGeoTransform(gt)
            out.GetRasterBand(1).SetNoDataValue(0); out.GetRasterBand(1).WriteArray(classified)
            out.SetMetadata({'CLASSIFICATION':method, 'CLASS_LABELS':json.dumps(labels,ensure_ascii=False),
                'SOURCE':str(path), 'DATE':'2015-12-07'})
            out.Close()
            with gdal.Open(str(output)) as check:
                np.testing.assert_array_equal(check.ReadAsArray(), classified)
            style = folder / (entry['name'] + '_continuo_classificado.qml')
            layer.saveNamedStyle(str(style))
            # Uma QML de classes usa codigos 1..N, nunca os limiares do raster continuo.
            categorical = QgsRasterLayer(str(output), entry['name']+' classes')
            class_shader = QgsColorRampShader(1, len(cuts))
            class_shader.setColorRampType(QgsColorRampShader.Discrete)
            style_items = shader.colorRampItemList()
            class_shader.setColorRampItemList([QgsColorRampShader.ColorRampItem(i+1,item.color,item.label)
                for i,item in enumerate(style_items)])
            raster_shader = QgsRasterShader(); raster_shader.setRasterShaderFunction(class_shader)
            categorical.setRenderer(QgsSingleBandPseudoColorRenderer(categorical.dataProvider(),1,raster_shader))
            categorical.saveNamedStyle(str(output.with_suffix('.qml')))
            area = abs(gt[1]*gt[5]-gt[2]*gt[4])/1e6
            rows = [{'code':i+1,'label':label,'pixels':int(np.count_nonzero(classified==i+1)),
                     'area_km2':float(np.count_nonzero(classified==i+1)*area)} for i,label in enumerate(labels)]
            assert sum(r['pixels'] for r in rows) == int(valid.sum())
            with (folder/(entry['name']+'_classes.csv')).open('w',encoding='utf-8',newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['code','label','pixels','area_km2'])
                writer.writeheader(); writer.writerows(rows)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == before
            result = {'name':entry['name'],'source':str(path),'style':str(style),'classified':str(output),
                'method':method,'valid_pixels':int(valid.sum()),'invalid_qa_retained':0,
                'percentiles':dict(zip(['min','p1','p5','p50','p95','p99','max'],
                    np.percentile(values[valid],[0,1,5,50,95,99,100]).tolist())),
                'classes':rows,'source_unchanged':True}
            if thermal:
                result['above_60_celsius'] = int(np.count_nonzero(valid & (values>60)))
                result['extreme_note'] = '60 Celsius e marcador exploratorio, nao criterio bibliografico de descarte.'
                extreme_path = folder/(entry['name']+'_acima60_revisar.tif')
                flags = np.zeros(values.shape,dtype=np.uint8); flags[valid]=1; flags[valid & (values>60)]=2
                raster(extreme_path,flags,epsg=31984,dtype=gdal.GDT_Byte)
                with gdal.Open(str(extreme_path),gdal.GA_Update) as flag_ds:
                    flag_ds.SetGeoTransform(gt); flag_ds.SetProjection(ds.GetProjection())
                result['extreme_mask'] = str(extreme_path)
            else:
                result['positive_pixels'] = int(np.count_nonzero(valid & (values>0)))
            report['products'].append(result)
    report['folder'] = str(folder)
    (folder/'REVISAO_CLASSIFICACAO.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def benchmark_index(baseline_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("baseline_engine", baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    with tempfile.TemporaryDirectory(prefix="satelite_benchmark_") as temporary:
        folder = Path(temporary)
        red = raster(folder / "red.tif", np.full((1024, 1024), 12000), 0.0000275, -0.2)
        nir = raster(folder / "nir.tif", np.full((1024, 1024), 24000), 0.0000275, -0.2)
        inputs = {"red": (30, red), "nir08": (30, nir)}
        times = {"baseline": [], "current": []}
        hashes = set()
        for name in ("baseline", "current", "current", "baseline", "baseline", "current"):
            stage = folder / f"run_{name}_{len(times[name])}"
            stage.mkdir()
            started = time.perf_counter()
            _, _, output, _ = (baseline if name == "baseline" else engine)._create_spectral_index(
                "landsat89", "ndvi", inputs, engine._raster_bounds(red), stage, stage)
            times[name].append(time.perf_counter() - started)
            with gdal.Open(str(output)) as dataset:
                hashes.add(hashlib.sha256(dataset.ReadAsArray().tobytes()).hexdigest())
        assert len(hashes) == 1, "Otimizacao alterou valores"
        print(json.dumps({"pixels": 1024 ** 2, "seconds": times,
                          "median_seconds": {key: float(np.median(values)) for key, values in times.items()},
                          "identical_values": True}, indent=2))


def verify_cbers_mosaic(output_folder, boundary_path):
    from contextlib import ExitStack
    from qgis.core import QgsRasterLayer, QgsVectorLayer, QgsFillSymbol
    output=Path(output_folder)
    report=json.loads((output/'MOSAICO'/'MOSAICO_CBers_TOA.json').read_text(encoding='utf-8'))
    counts=[0]*len(report['scenes']); samples=0
    with ExitStack() as stack:
        merged={key:stack.enter_context(gdal.Open(path)) for key,path in report['outputs'].items()}
        originals=[{key:stack.enter_context(gdal.Open(path)) for key,path in scene['outputs'].items()}
                   for scene in report['scenes']]
        ref=merged['SCENE_INDEX'];gt=ref.GetGeoTransform()
        for row in range(0,ref.RasterYSize,256):
            height=min(256,ref.RasterYSize-row)
            provenance=ref.ReadAsArray(0,row,ref.RasterXSize,height)
            for index,source in enumerate(originals):
                ys,xs=np.nonzero(provenance==index+1)
                counts[index]+=int(xs.size)
                if not xs.size: continue
                # Amostra deterministica por bloco/cena, comparada com os arquivos originais.
                for position in np.linspace(0,len(xs)-1,min(8,len(xs)),dtype=int):
                    x,y=int(xs[position]),int(ys[position])+row
                    original_gt=source['BAND3'].GetGeoTransform()
                    ox=int(round((gt[0]-original_gt[0])/8))+x
                    oy=int(round((original_gt[3]-gt[3])/8))+y
                    values={}
                    for key in ('BAND1','BAND2','BAND3','BAND4','ndvi','savi','ndwi'):
                        value=float(merged[key].ReadAsArray(x,y,1,1)[0,0])
                        expected=float(source[key].ReadAsArray(ox,oy,1,1)[0,0])
                        assert value==expected, (key,index,x,y,value,expected)
                        values[key]=value
                    nir,red,green=values['BAND4'],values['BAND3'],values['BAND2']
                    l=report['scenes'][index]['savi_l']
                    assert abs(values['ndvi']-(nir-red)/(nir+red))<1e-6
                    assert abs(values['savi']-(1+l)*(nir-red)/(nir+red+l))<1e-6
                    assert abs(values['ndwi']-(green-nir)/(green+nir))<1e-6
                    samples+=1
    assert samples and all(counts), 'As duas cenas devem contribuir ao mosaico.'
    audit={'samples_compared':samples,'pixels_per_scene_inside_polygon':counts,
           'same_scene_all_bands':True,'equations_tolerance':1e-6,
           'coverage_inside_polygon':float(report['coverage_inside_polygon'])}
    (output/'AUDITORIA_PIXELS.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    project=QgsProject();project.setCrs(QgsCoordinateReferenceSystem('EPSG:31984'))
    boundary=QgsVectorLayer(boundary_path,'Feira de Santana - limite OSM','ogr')
    assert boundary.isValid()
    boundary.renderer().setSymbol(QgsFillSymbol.createSimple({'color':'0,0,0,0',
        'outline_color':'40,40,40,255','outline_width':'0.4'}))
    layers={}
    for key in ('ndvi','savi','ndwi','SCENE_INDEX'):
        layer=QgsRasterLayer(report['outputs'][key],key.upper()+' CBERS TOA - cobertura parcial 30,72%')
        assert layer.isValid()
        if key!='SCENE_INDEX':
            _,ok=layer.loadNamedStyle(str(Path(report['outputs'][key]).with_suffix('.qml')))
            assert ok
        project.addMapLayer(layer);layers[key]=layer
        project.layerTreeRoot().findLayer(layer.id()).setItemVisibilityChecked(key=='ndvi')
    project.addMapLayer(boundary)
    extent=QgsCoordinateTransform(boundary.crs(),project.crs(),project).transformBoundingBox(boundary.extent())
    extent.scale(1.08)
    project.viewSettings().setDefaultViewExtent(__import__('qgis.core',fromlist=['QgsReferencedRectangle']).QgsReferencedRectangle(extent,project.crs()))
    assert project.write(str(output/'Feira_CBers_Multicena_Teste.qgz'))
    settings=QgsMapSettings();settings.setDestinationCrs(project.crs())
    settings.setLayers([boundary,layers['ndvi']]);settings.setExtent(extent)
    settings.setOutputSize(QSize(1100,1000));settings.setBackgroundColor(QColor('white'))
    job=QgsMapRendererSequentialJob(settings);job.start();job.waitForFinished()
    assert job.renderedImage().save(str(output/'NDVI_COBERTURA.png'))
    print(json.dumps(audit,indent=2),flush=True)


def live_cbers_coverage(boundary_path, start_year, end_year, output_path):
    from qgis.core import QgsVectorLayer,QgsCoordinateTransform
    from osgeo import ogr
    boundary=QgsVectorLayer(boundary_path,'limite_cbers','ogr')
    if not boundary.isValid(): raise RuntimeError('Delimitacao invalida.')
    transform=QgsCoordinateTransform(boundary.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject())
    geometry=None
    for feature in boundary.getFeatures():
        part=feature.geometry();part.transform(transform)
        shape=ogr.CreateGeometryFromJson(part.asJson());geometry=shape if geometry is None else geometry.Union(shape)
    report=engine.search_cbers_wpm_coverage(geometry,start_year,end_year)
    Path(output_path).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)


def live_drone(source, output):
    from imagens_satellite.provider import DroneOrthomosaicAlgorithm
    algorithm=DroneOrthomosaicAlgorithm();algorithm.initAlgorithm();feedback=QgsProcessingFeedback()
    result,ok=algorithm.run({'PRODUCT':0,'RED':source,'RED_BAND':1,'GREEN':source,'GREEN_BAND':2,
        'BLUE':source,'BLUE_BAND':3,'SCALE':1.0,'OFFSET':0.0,'CLASSIFICATION':2,
        'CLASS_COUNT':5,'OUTPUT':output},QgsProcessingContext(),feedback)
    if not ok: raise RuntimeError(feedback.textLog())
    print(json.dumps({'result':result,'log':feedback.textLog()},ensure_ascii=False,indent=2),flush=True)


def live_cbers_mosaic(boundary_path, output_folder, max_scenes=3):
    from osgeo import ogr
    from urllib.request import Request
    from urllib.parse import urlencode
    from qgis.core import QgsVectorLayer
    from imagens_satellite.provider import SateliteCadenteProvider
    import processing
    provider=SateliteCadenteProvider()
    QgsApplication.processingRegistry().addProvider(provider)
    boundary=QgsVectorLayer(boundary_path,'limite_teste','ogr')
    if not boundary.isValid(): raise RuntimeError('Delimitacao invalida.')
    transform=QgsCoordinateTransform(boundary.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject())
    extent=transform.transformBoundingBox(boundary.extent())
    bounds=(extent.xMinimum(),extent.yMinimum(),extent.xMaximum(),extent.yMaximum())
    geometry=None
    for feature in boundary.getFeatures():
        g=feature.geometry();g.transform(transform)
        shape=ogr.CreateGeometryFromJson(g.asJson())
        geometry=shape if geometry is None else geometry.Union(shape)
    output=Path(output_folder);output.mkdir(parents=True,exist_ok=False)
    payload=urlencode({'bbox':','.join(map(str,bounds)),
        'datetime':'2019-01-01T00:00:00Z/2026-10-01T23:59:59Z','limit':100})
    scenes=engine._stac_features(Request('https://data.inpe.br/bdc/stac/v1/collections/CB4A-WPM-L4-DN-1/items?'+payload),None)
    ranked=[(ogr.CreateGeometryFromJson(json.dumps(s['geometry'])).Intersection(geometry),s) for s in scenes]
    remaining=geometry.Clone();selected=[]
    for _ in range(max_scenes):
        choices=[(shape.Intersection(remaining).GetArea(),s['properties']['datetime'],shape,s)
                 for shape,s in ranked if s['id'] not in {i['id'] for i in selected}]
        if not choices: break
        _,_,shape,scene=max(choices,key=lambda x:(x[0],x[1]))
        selected.append(scene);remaining=remaining.Difference(shape)
    if len(selected)<2: raise RuntimeError('Menos de duas cenas distintas disponiveis.')
    (output/'SELECAO.json').write_text(json.dumps({'scenes':selected,'bounds':bounds,
        'geometric_coverage':1-remaining.GetArea()/geometry.GetArea(),
        'warning':'Cobertura geometrica em graus; nao avalia nuvens. Limite OSM, nao IBGE.'},indent=2),encoding='utf-8')
    print('SELECAO',[s['id'] for s in selected],flush=True)
    feedback=QgsProcessingFeedback()
    feedback.progressChanged.connect(lambda value: None)
    toa=[]
    for index,scene in enumerate(selected):
        print('DOWNLOAD',scene['id'],flush=True)
        dn=output/f'DN_{index+1}';calibrated=output/f'TOA_{index+1}'
        processing.run('imagens_satellite:cbers4a_wpm',{'EXTENT':f'{bounds[0]},{bounds[2]},{bounds[1]},{bounds[3]} [EPSG:4326]',
            'YEAR':int(scene['properties']['datetime'][:4]),'SCENE_ID':scene['id'],'PAN':False,'OUTPUT':str(dn)},feedback=feedback)
        print('CALIBRACAO',scene['id'],flush=True)
        processing.run('imagens_satellite:cbers4a_toa',{'BANDS_FOLDER':str(dn),'ESUN':'1958;1852;1559;1091',
            'SAVI_L':.5,'OUTPUT':str(calibrated)},feedback=feedback)
        toa.append(str(calibrated))
    print('MOSAICO',flush=True)
    result=processing.run('imagens_satellite:cbers4a_mosaico_toa',{'FOLDERS':'\n'.join(toa),
        'MASK':boundary,'OUTPUT':str(output/'MOSAICO')},feedback=feedback)
    report=json.loads(Path(result['REPORT']).read_text(encoding='utf-8'))
    checks={}
    for key in ('NDVI','SAVI','NDWI','SCENE_INDEX'):
        with gdal.Open(result[key]) as ds:
            count=0;minimum=float('inf');maximum=float('-inf')
            for row in range(0,ds.RasterYSize,512):
                a=ds.GetRasterBand(1).ReadAsArray(0,row,ds.RasterXSize,min(512,ds.RasterYSize-row))
                valid=a[a!=-9999];count+=int(valid.size)
                if valid.size: minimum=min(minimum,float(valid.min()));maximum=max(maximum,float(valid.max()))
            assert count>0
            checks[key]={'valid_pixels':count,'min':minimum,'max':maximum,
                'pixel_m':abs(ds.GetGeoTransform()[1]),'qml':Path(result[key]).with_suffix('.qml').is_file()}
            assert checks[key]['pixel_m']==8
    checks['coverage_inside_polygon']=report['coverage_inside_polygon']
    checks['outputs']=result
    (output/'VERIFICACAO.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    print(json.dumps(checks,indent=2),flush=True)


if __name__ == "__main__":
    app = QgsApplication([], False)
    app.initQgis()
    sys.path.append(str(Path(QgsApplication.prefixPath()) / "python" / "plugins"))
    gdal.UseExceptions()
    if '--verify-cbers-mosaic' in sys.argv:
        parser=argparse.ArgumentParser()
        parser.add_argument('--verify-cbers-mosaic',required=True)
        parser.add_argument('--boundary',required=True)
        args=parser.parse_args()
        verify_cbers_mosaic(args.verify_cbers_mosaic,args.boundary)
    elif '--cbers-coverage' in sys.argv:
        parser=argparse.ArgumentParser()
        parser.add_argument('--cbers-coverage',required=True)
        parser.add_argument('--start-year',type=int,default=2019)
        parser.add_argument('--end-year',type=int,default=2026)
        parser.add_argument('--output',required=True)
        args=parser.parse_args()
        live_cbers_coverage(args.cbers_coverage,args.start_year,args.end_year,args.output)
    elif '--live-cbers-mosaic' in sys.argv:
        parser=argparse.ArgumentParser()
        parser.add_argument('--live-cbers-mosaic',required=True,help='Camada de delimitacao OGR')
        parser.add_argument('--output',required=True)
        parser.add_argument('--max-scenes',type=int,default=3,choices=range(2,9))
        args=parser.parse_args()
        live_cbers_mosaic(args.live_cbers_mosaic,args.output,args.max_scenes)
    elif '--live-drone' in sys.argv:
        parser=argparse.ArgumentParser()
        parser.add_argument('--live-drone',required=True,help='Ortomosaico RGB georreferenciado')
        parser.add_argument('--output',required=True)
        args=parser.parse_args()
        live_drone(args.live_drone,args.output)
    elif "--live" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--live", choices=tuple(engine.MULTIBAND_SOURCES), required=True)
        parser.add_argument("--workers", type=int, choices=range(1, 5), default=engine.ANALYTIC_DOWNLOAD_WORKERS)
        parser.add_argument("--month-start", type=int, default=1)
        parser.add_argument("--month-end", type=int, default=12)
        args = parser.parse_args()
        live_smoke(args.live, args.workers, args.month_start, args.month_end)
    elif "--benchmark-index" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--benchmark-index", required=True)
        benchmark_index(parser.parse_args().benchmark_index)
    else:
        unittest.main(verbosity=2)

