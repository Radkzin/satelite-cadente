# Satelite Cadente 3.0.0 - instalar e usar em outro PC

## Requisitos

- QGIS com Processing habilitado. Ambiente de desenvolvimento: QGIS 4.2.0,
  Python 3.12 e GDAL 3.13.1; outras versoes precisam de verificacao.
- Use Python, GDAL e NumPy da propria instalacao QGIS.
- Internet para catalogos/downloads e espaco em disco para GeoTIFFs.
- Conta/token NASA Earthdata gratuito apenas para baixar ECOSTRESS. A busca
  publica ao catalogo funciona sem conta.

## Instalar pelo GitHub

1. Abra [Releases](https://github.com/Radkzin/satelite-cadente/releases/latest)
   e baixe o ZIP instalavel da versao mais nova.
2. No QGIS, abra **Complementos > Gerenciar e Instalar Complementos >
   Instalar a partir de ZIP** e selecione o arquivo.
3. Ative **Satelite Cadente** e confira os nove algoritmos Processing.
4. Se substituir uma versao anterior, reinicie o QGIS para carregar o codigo novo.

O ZIP instalavel contem a pasta imagens_satellite diretamente na raiz.
O arquivo Source code gerado pelo GitHub nao e o pacote instalavel.

## HLS: mosaico e indice

1. Carregue o limite e abra **Satelite Cadente > Obter imagens**.
2. Selecione **NASA HLS v2 L30** ou **S30**, extensao, ano/meses e pasta
   permanente; baixe o perfil apropriado.
3. Abra **Gerar produtos**, indique a pasta de bandas, fonte HLS, indice,
   limite poligonal e CRS projetado adequado.
4. Escolha classificacao, numero de classes e saida. Confira GeoTIFF, QML,
   relatorio JSON e metadados.

HLS tem grade de 30 m, reflectancia escalada por 0,0001 e mascara Fmask. O NIR
do S30 e B8A. Indices HLS podem incluir NDVI, SAVI, EVI2, NDWI e NDBI conforme
as bandas. HLS melhora comparabilidade temporal, nao detalhe espacial em
relacao ao Sentinel-2 L2A a 10 m.

## ECOSTRESS: consultar e preparar LST

1. Abra **Obter imagens > NASA ECOSTRESS: consultar e baixar L2T**; use ID de
   granulo vazio para gravar o catalogo e conferir IDs/datas.
2. No gerenciador de autenticacao do QGIS, crie configuracao **Basica**:
   usuario **Earthdata**, senha = token pessoal Earthdata. Nao use a senha da
   conta. Autorize o acesso ao LP DAAC conforme instrucoes da NASA.
3. Reexecute a busca com o ID exato e a configuracao de autenticacao. Nunca
   envie o token por chat nem o grave no projeto.
4. Use **Gerar produtos > NASA ECOSTRESS: preparar LST** e selecione os TIFFs
   LST, QC e cloud originais do mesmo granulo. Sao exigidos LST Float32 Kelvin,
   CRS UTM e grade nativa de 70 m.
5. O complemento converte Kelvin para Celsius, mantem apenas pixels cloud=0
   e QC bits 0-3 iguais a zero, sem preencher lacunas. Verifique cobertura,
   metadados e relatorio.

A LST representa temperatura de superficie no horario de passagem, nao do ar.
Os filtros QA/cloud nao removem todas as fontes de incerteza. O fluxo de
download autenticado ainda precisa ser validado pelo usuario com sua conta.

## CBERS, drone e Landsat

- Landsat: download Level-2, produtos opticos, LST e TsHARP estimado.
- CBERS-4A: consultar cobertura, baixar DN WPM, calibrar TOA experimental e
  mosaicar se necessario. TOA nao e reflectancia de superficie.
- Drone: usar ortomosaico GeoTIFF georreferenciado. VARI aceita RGB;
  NDVI/SAVI/NDWI exigem NIR real e reflectancia calibrada.
- Mosaicos web: contexto visual; nao usar para indices espectrais.

## Solucao de problemas

- Sem cenas: ampliar periodo/area e revisar filtros.
- Sem pixel valido: revisar bandas, QA, nuvens, CRS e NoData.
- ECOSTRESS HTTP 401/403: validar token, conta Earthdata e autorizacao LP DAAC.
- Raster deslocado: revisar CRS da imagem, limite e saida.
- Limites manuais: usar valores numericos crescentes separados por ponto e virgula.

As referencias, citacoes e paginas dos PDFs consultados estao em
imagens_satellite/REFERENCIAS_METODOLOGIA_IMAGENS_SATELITE.md. Os PDFs
originais nao acompanham o complemento.

