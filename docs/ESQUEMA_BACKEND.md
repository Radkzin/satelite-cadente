# Esquema de backend - Satelite Cadente 3.0.0

O complemento nao possui API, servidor ou banco de dados proprio. O
processamento ocorre localmente no QGIS.

Interface QGIS / Processing
    -> plugin.py
    -> provider.py
    -> satellite_mosaic_qgis.py
       -> catalogos e arquivos remotos
       -> GDAL + NumPy
       -> GeoTIFF / QML / JSON

## Integracoes

| Fonte | Acesso | Uso |
| --- | --- | --- |
| Element 84 Earth Search | STAC HTTPS | Sentinel-2 L2A. |
| Microsoft Planetary Computer | STAC HTTPS e SAS | Landsat Level-2 e espelho NASA HLS v2 L30/S30. |
| NASA CMR / LP DAAC | CMR HTTPS e download autenticado | Catalogo e arquivos ECOSTRESS L2T LSTE.003. |
| INPE BDC | STAC/HTTPS | CBERS-4A WPM e metadados de cena. |
| EOX, Esri, Google, Bing | Tiles HTTPS | Contexto visual, sujeito aos termos de cada provedor. |

## Contratos de dados

- BANDAS_MANIFEST.json: fonte, CRS, perfil, bandas, escalas, cenas/datas,
  resolucoes, QA e cobertura.
- ECOSTRESS_CATALOGO.json e ECOSTRESS_ORIGEM.json: granulos, ativos, datas e
  origem, sem token.
- ECOSTRESS raster de saida: Float32 Celsius, 70 m, NoData -9999, insumos,
  CRS, referencia, QC/cloud e limitacoes nos metadados.
- GeoTIFF de indices: valor numerico e metadados; classificacao e cortes no
  QML/JSON, sem alterar os valores.

Os caminhos sao escolhidos pelo usuario e nao dependem de D:\GIS. Dados
orbitais seguem atribuicoes/licencas dos provedores. PDFs de pesquisa nao sao
copiados ao repositorio.

## Limites de confianca

Catalogos, ativos e rede podem falhar. Validacoes locais conferem escala,
bandas, mascara, geometria, autenticacao e integridade basica dos arquivos.
Isso nao substitui validacao de campo nem avaliacao independente da qualidade
do produto.

