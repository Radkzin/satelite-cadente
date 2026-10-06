# PRD - Satelite Cadente 3.0.0

## Problema e objetivo

Analistas de geoprocessamento precisam adquirir imagens orbitais e produzir
indices rastreaveis dentro do QGIS. O complemento integra fontes opticas,
termicas e de drone sem prometer cobertura ou precisao alem dos dados de
entrada.

## Requisitos funcionais

| ID | Entrega |
| --- | --- |
| RF1 | Consultar Sentinel-2, Landsat e NASA HLS por extensao, periodo e filtros disponiveis. |
| RF2 | Baixar bandas e registrar cenas, datas, qualidade, escala e cobertura no manifesto. |
| RF3 | Gerar RGB, NDVI, SAVI, EVI2, NDWI, NDBI e LST conforme sensor e bandas disponiveis. |
| RF4 | Recortar por poligono, reprojetar para SRC metrico e preservar NoData. |
| RF5 | Classificar visualmente por intervalos iguais, quantis, Jenks, desvio-padrao ou limites manuais; criar rotulos e QML. |
| RF6 | Gravar metadados, GeoTIFF e relatorio JSON junto as saidas. |
| RF7 | Calcular VARI em ortomosaico RGB; NDVI/SAVI/NDWI exigem NIR e reflectancia calibrada. |
| RF8 | Consultar CMR e baixar um granulo NASA ECOSTRESS L2T LSTE escolhido, com token Earthdata protegido pelo QGIS. |
| RF9 | Preparar LST ECOSTRESS de 70 m Celsius com QC, mascara de nuvem e insumos do mesmo granulo. |
| RF10 | Buscar e processar CBERS-4A WPM, com calibracao TOA experimental e mosaico multicena. |
| RF11 | Oferecer menu superior com dois submenus e nove algoritmos Processing. |

## Requisitos de qualidade

- Nao sobrescrever saidas existentes sem aviso; manter insumos originais.
- Nuvens, sombras, pixels sem observacao e areas fora da cobertura permanecem NoData.
- Informar cobertura efetiva, resolucao, CRS, fontes, metodo e limitacoes nos metadados/relatorios.
- Credenciais nunca entram em arquivos de projeto, manifesto ou log.
- Separar classificacao de simbologia de classificacao validada de uso do solo.

## Limites

HLS L30/S30 entrega reflectancia harmonizada a 30 m, com Fmask; melhora
disponibilidade temporal e consistencia entre sensores, nao a nitidez espacial
do Sentinel-2 L2A a 10 m. HLS nao fornece LST.

ECOSTRESS L2T LSTE fornece temperatura de superficie no horario de passagem,
nao temperatura do ar. QA/cloud nao removem toda incerteza, e cada granulo
pode cobrir apenas parte da area. A autenticacao Earthdata e necessaria para
baixar; a consulta ao catalogo e publica.

CBERS TOA e experimental e nao equivale a reflectancia de superficie. PAN e
reamostragem nao criam resolucao multiespectral. TsHARP e estimativa termica.
Indices e classes, sozinhos, nao comprovam uso do solo nem estabilidade
ecodinamica.

## Aceite

1. QGIS lista nove algoritmos do provedor imagens_satellite.
2. HLS registra fonte, escala, Fmask, datas e cobertura nos metadados.
3. ECOSTRESS recusa arquivos de granulos/grades incompativeis e preserva
   NoData apos QC e cloud.
4. Produtos raster incluem metadados, QML/rotulos quando classificado e JSON.
5. Workflow de tag so publica ZIP quando a versao da tag coincide com
   metadata.txt.

O download ECOSTRESS com credenciais reais ainda requer validacao no ambiente
do usuario; isso nao e declarado como teste concluido nesta versao.

