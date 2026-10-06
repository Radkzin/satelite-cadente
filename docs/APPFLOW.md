# APPFLOW - Satelite Cadente 3.0.0

## Entrada

Ativar o complemento -> usar Satelite Cadente na barra de menus ou abrir um
dos nove algoritmos pela Caixa de Ferramentas de Processamento.

- Obter imagens: Sentinel-2, Landsat, HLS e mosaicos web; ECOSTRESS
  consultar/baixar; separador; CBERS-4A consultar cobertura e baixar WPM.
- Gerar produtos: indices/produtos Sentinel-2, Landsat e HLS; ECOSTRESS
  preparar LST; indices de drone; separador; CBERS-4A calibrar TOA e montar
  mosaico TOA.

As acoes abrem dialogos Processing sem iniciar processamento automaticamente.
O atalho da barra abre o fluxo principal de aquisicao. Desativar remove menu,
submenus, acoes, atalho e provedor, sem remover camadas.

## Fluxo optico HLS/Sentinel/Landsat

1. Carregar limite e definir extensao, fonte, periodo e pasta permanente.
2. Baixar as bandas requeridas; conferir manifesto, datas, qualidade e
   cobertura efetiva.
3. Escolher produto analitico, limite poligonal, CRS projetado e classificacao.
4. Conferir GeoTIFF, metadados, QML/rotulos e JSON. NoData permanece sem classe.

HLS L30/S30 e 30 m. Para o NIR do S30 usa-se B8A. A composicao HLS melhora
consistencia temporal, nao substitui o detalhe espacial do Sentinel-2 de 10 m.
Para comparar indices entre datas, mantenha sazonalidade e limites justificados.

## Fluxo ECOSTRESS

1. Execute NASA ECOSTRESS: consultar e baixar L2T com ID do granulo vazio.
   Confira a lista e cobertura parcial no catalogo JSON.
2. Configure no gerenciador de autenticacao do QGIS o metodo Basico, usuario
   Earthdata e token pessoal no campo Senha. A conta/token sao gratuitos.
3. Reexecute com o ID exato do granulo escolhido; o token nao deve ser colocado
   nos parametros livres, projeto, log ou conversa.
4. Em Gerar produtos, selecione os TIFFs originais LST, QC e cloud do mesmo
   granulo. O algoritmo exige grade nativa UTM de 70 m, converte Kelvin para
   Celsius e aplica QC/cloud. Veja relatorio, classes e cobertura.

ECOSTRESS mede LST instantanea, nao temperatura do ar. Nao combine horarios
como se fossem uma unica cena. Download autenticado ainda precisa de validacao
com credenciais reais do usuario.

## Outros fluxos

- CBERS-4A: consultar cobertura -> baixar DN WPM -> calibrar TOA
  experimental -> mosaicar se necessario. Inspecionar nuvens/lacunas.
- Drone: usar ortomosaico georreferenciado. VARI aceita RGB; NDVI/SAVI/NDWI
  exigem NIR real e reflectancia calibrada. Fotos soltas requerem fotogrametria.
- Mosaicos web: apenas contexto visual; nao calcular indices cientificos.

## Recuperacao

- Sem cenas: ampliar periodo ou area e rever filtros.
- Sem dados validos: verificar bandas, QA, NoData, CRS e cobertura.
- Erro Earthdata: conferir token, autorizacao LP DAAC e nome/versao do granulo.
- Classificacao manual: informar cortes numericos crescentes separados por ;.
- Antes de comparar periodos, igualar metodo, mascara e limiares justificados.

