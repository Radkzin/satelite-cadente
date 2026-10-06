# TRD - Satelite Cadente 3.0.0

## Plataforma

Complemento Python para QGIS com provedor Processing. O metadata.txt declara
QGIS 3.28-4.99 e suporte Qt 6. Ambiente de desenvolvimento: QGIS 4.2.0,
Python 3.12.13 e GDAL 3.13.1 no Windows. Dependencias de processamento sao as
distribuidas com QGIS: PyQGIS, Processing, NumPy e GDAL/OGR/OSR.

## Modulos

| Arquivo | Responsabilidade |
| --- | --- |
| __init__.py | Entrada classFactory do QGIS. |
| plugin.py | Menu superior, submenus, acoes e ciclo de vida do provedor. |
| provider.py | Nove algoritmos Processing, parametros e pos-processamento. |
| satellite_mosaic_qgis.py | Catalogos, downloads, QA, GDAL, indices, metadados e relatorios. |
| metadata.txt | Identidade, versao 3.0.0, autor e links do GitHub. |

## Algoritmos

mosaico_satellite, produto_analitico, drone_ortomosaico_indices,
cbers4a_buscar_cobertura, cbers4a_wpm, cbers4a_toa,
cbers4a_mosaico_toa, ecostress_buscar, ecostress_lst.

## Fluxos de dados

1. Sentinel-2 L2A e Landsat Level-2 consultam catalogos STAC; bandas e QA sao
   alinhadas em grade e o manifesto preserva datas, resolucao, calibracao e
   cena por pixel.
2. HLS L30/S30 consulta as colecoes hls2-l30/hls2-s30 no espelho publico
   Microsoft Planetary Computer. Reflectancia usa escala 0,0001; Fmask mascara
   pixels invalidos. O NIR de HLS S30 e B8A. Grade nativa: 30 m; sem produto LST.
3. ECOSTRESS consulta NASA CMR para ECO_L2T_LSTE.003. Download autenticado
   exige token Earthdata em autenticacao do QGIS e usa somente um granulo
   escolhido. LST, QC e cloud precisam pertencer a mesma aquisicao e grade
   UTM nativa de 70 m. Converte Kelvin para Celsius, aplica cloud=0 e QC bits
   0-3 iguais a zero, sem interpolacao ou preenchimento de lacunas.
4. O motor produz GeoTIFF com metadados, QML de classificacao e relatorio JSON.
   Saidas existentes nao devem ser substituidas silenciosamente.

## Interface e ciclo de vida

plugin.py cria o menu textual Satelite Cadente com Obter imagens e
Gerar produtos. Acoes com icones do tema QGIS apenas abrem os formularios
Processing. Acoes e submenus pertencem ao respetivo menu; unload() remove
atalho, menus e provedor sem apagar camadas ou arquivos do projeto.

## Seguranca e limitacoes

Token Earthdata e mantido na autenticacao protegida pelo QGIS e enviado somente
ao fluxo de download. URLs de download sao restritas a HTTPS e host LP DAAC;
arquivos parciais sao temporarios. Nao envie tokens em chat ou logs.

HLS harmoniza reflectancia, nao aumenta resolucao espacial nem fornece LST.
ECOSTRESS QA nao e validacao independente da incerteza. CBERS TOA e
experimental; mosaicos multitemporais podem misturar datas e precisam de
interpretacao cuidadosa.

## Distribuicao

O GitHub Actions executa em tags v*, valida a correspondencia com metadata.txt
e cria Satelite_Cadente_<versao>.zip com imagens_satellite/ na raiz. Os PDFs
originais de pesquisa e o SVG externo sem licenca confirmada nao entram na
distribuicao.

