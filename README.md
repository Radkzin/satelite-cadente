# Satelite Cadente

Complemento open source PyQGIS de Raphael S. / [Radkzin](https://github.com/Radkzin).
Codigo sob [GNU GPL v3](LICENSE). Versao **3.0.0**.

## Instalar

Quando houver um Release, baixe o ZIP instalavel em
[Releases](https://github.com/Radkzin/satelite-cadente/releases/latest) e use
**Complementos > Gerenciar e Instalar Complementos > Instalar a partir de ZIP**.
O ZIP instalavel contem a pasta imagens_satellite na raiz; o ZIP de codigo-fonte
automatico do GitHub nao tem essa estrutura.

O workflow .github/workflows/release.yml e disparado por tags v*, confere
se a tag coincide com metadata.txt, monta o ZIP instalavel e o anexa ao
GitHub Release. Exemplo de tag: v3.0.0.

QGIS 4.2.0, Python 3.12 e GDAL 3.13.1 foram usados no ambiente de
desenvolvimento. O complemento declara QGIS 3.28-4.99; outras combinacoes
precisam ser verificadas. Use as dependencias da instalacao QGIS.

## Fontes e produtos

- Sentinel-2 L2A e Landsat Collection 2 Level-2: bandas, RGB, indices e
  temperatura conforme sensor, com recorte, classificacao visual, rotulos,
  QML e relatorio JSON.
- NASA HLS v2 L30/S30: reflectancia de superficie harmonizada, 30 m, acesso
  pelo catalogo publico Planetary Computer. HLS amplia a disponibilidade e
  comparabilidade temporal; nao tem a nitidez do Sentinel-2 a 10 m. O NIR do
  HLS S30 e B8A, a escala e 0,0001 e Fmask controla pixels de qualidade ruim.
- NASA ECOSTRESS L2T LSTE: consulta publica ao catalogo CMR; download de um
  granulo escolhido exige token Earthdata configurado na autenticacao do QGIS.
  Prepara LST Celsius a 70 m usando LST, QC e cloud do mesmo granulo.
- CBERS-4A WPM: busca de cobertura, download DN, calibracao TOA experimental
  e mosaico. Drone: VARI em RGB; NDVI/SAVI/NDWI exigem NIR e reflectancia
  calibrada. Mosaicos web servem apenas para referencia visual.

## Limites

HLS nao fornece LST. Um mosaico multitemporal nao e uma serie temporal pronta;
para comparar datas, use janelas sazonais e limites de classificacao
justificados e equivalentes. ECOSTRESS mede temperatura de superficie em um
horario de passagem, nao temperatura do ar; QA/cloud nao removem toda
incerteza, e lacunas permanecem NoData. O download autenticado ECOSTRESS ainda
precisa de validacao com credenciais reais do usuario.

Classes de simbologia nao comprovam uso do solo. PAN de 2 m e reamostragem nao
criam resolucao multiespectral. CBERS TOA e experimental, e TsHARP e uma
estimativa, nao nova medicao termica. Valide cobertura, datas e calibracao
antes de interpretacao quantitativa.

## Documentacao

- [Tutorial para outro computador](docs/TUTORIAL_INSTALACAO_E_USO.md)
- [PRD](docs/PRD.md), [TRD](docs/TRD.md), [APPFLOW](docs/APPFLOW.md)
- [Esquema de backend](docs/ESQUEMA_BACKEND.md)
- [Plano de implementacao](docs/PLANO_DE_IMPLEMENTACAO.md)
- [Referencias metodologicas](imagens_satellite/REFERENCIAS_METODOLOGIA_IMAGENS_SATELITE.md)

Os PDFs originais de pesquisa nao sao redistribuidos. Consulte no documento
de referencias as citacoes e paginas que fundamentam cada metodologia.

## Testes e autoria

Os testes de regressao estao em tests/test_satelite_cadente.py e devem ser
executados em processo separado com o Python da instalacao QGIS. Os modos
--live hls_l30 e --live hls_s30 fazem requisicoes reais; nao sao necessarios
para empacotar o complemento. HLS segue os [produtos e algoritmos NASA](https://hls.gsfc.nasa.gov/);
ECOSTRESS segue o [produto NASA/JPL L2T LSTE](https://doi.org/10.5067/ECOSTRESS/ECO_L2T_LSTE.003).

Codigo: [github.com/Radkzin/satelite-cadente](https://github.com/Radkzin/satelite-cadente).
Relate erros em [Issues](https://github.com/Radkzin/satelite-cadente/issues).
Dados orbitais permanecem sujeitos as licencas e atribuicoes de seus provedores.

