# Referências metodológicas — Satelite Cadente

## Integracao na versao 2.4.0

O algoritmo de produto aceita limite poligonal e SRC projetado metrico,
recorta/reforca NoData e classifica apenas o resultado final. Mantem bandas
originais intactas. Para temperatura, opcao explicita de intervalos de 1
Celsius conforme `submissao-2752-arquivo-11261-1.pdf`, p. 3 (impressa 96).
Nao reaplica a cadeia radiancia/Planck Level-1 do artigo ao ST Collection 2
Level-2. Rotulos sao relativos, com limites efetivos em QML/JSON.

SAVI usa L configuravel, padrao 0,5, conforme `Aplicacaoindices.pdf`, p. 4 e 6.
SAVI e NDVI compartilham uma paleta legivel, mas nunca cortes universais.
Os perfis de download enxutos sao otimizacao de engenharia, nao metodologia
atribuida aos artigos: mantem QA/SCL e coerencia por pixel para as bandas
solicitadas, sem exigir SWIR/termal quando so se deseja vegetacao.

O relatorio JSON registra cenas, pixels/areas planimetricas por classe,
resolucao e cobertura dentro do limite. O marcador acima de 60 Celsius e
exploratorio desta revisao, nao filtro validado por literatura; nao exclui
valores. Nao testa ST_QA, distancia a nuvens ou QA_RADSAT. A fusao continua
estimada, com validacao na grade agregada nativa e nao na grade fina recortada.
Datas diferentes e mosaicos multitemporais nao isolam ganhos de resolucao.

## Acervo Processamento aplicado na versao 2.3.0

Revisao de 01/10/2026: `ANALISE_METODOLOGIA_PROCESSAMENTO.md` registra a triagem dos 17 PDFs, paginas consultadas, duplicatas e limites de aplicacao.

- `Aplicacaoindices.pdf`, p. 2: comparacao de epoca seca e umida; p. 4: equacao SAVI e fator L; p. 6: uso de L=0,5. Aplicacoes: busca por meses e SAVI com L configuravel (0 a 1, padrao 0,5).
- `dorlivete,+e47611122583.pdf`, p. 5: fator de ajuste relacionado a cobertura e analise multitemporal.
- `MET-479-Waters-et-al-SEBAL.pdf`, p. 19: SAVI=(1+L)*(NIR-Red)/(NIR+Red+L), com equivalencia ao NDVI quando L=0. O complemento nao executa SEBAL completo.
- `Guide_to_GIS_and_Image_Processing_Volume_2.pdf`, p. 132: compatibilidade das grades para operacoes entre imagens. A otimizacao de reutilizar bandas ja alinhadas e uma decisao de engenharia, nao um benchmark atribuido ao manual.

O valor efetivo de L fica no GeoTIFF; meses e ano ficam no manifesto. As descricoes anteriores com L=0,5 representam o padrao, nao um valor obrigatorio. Limiares de NDVI nao devem ser transferidos automaticamente ao SAVI.

## Produtos implementados

- **Mosaico RGB padrão**: composição visual contextual. Não é índice espectral e não deve ser usada como substituta de bandas de reflectância.
- **NDVI**: `(NIR - Vermelho) / (NIR + Vermelho)`. Rouse, J. W. et al. (1974). *Monitoring vegetation systems in the Great Plains with ERTS*. Third ERTS Symposium, p. 309-317.
- **NDWI**: `(Verde - NIR) / (Verde + NIR)`. McFeeters, S. K. (1996). *The use of the Normalized Difference Water Index (NDWI) in the delineation of open water features*. International Journal of Remote Sensing, 17(7), 1425-1432. DOI: [10.1080/01431169608948714](https://doi.org/10.1080/01431169608948714).
- **NDBI**: `(SWIR1 - NIR) / (SWIR1 + NIR)`. Zha, Y.; Gao, J.; Ni, S. (2003). *Use of normalized difference built-up index in automatically mapping urban areas from TM imagery*. International Journal of Remote Sensing, 24(3), 583-594. DOI: [10.1080/01431160304987](https://doi.org/10.1080/01431160304987).
- **SAVI**: `(1+L) × (NIR - Vermelho) / (NIR + Vermelho + L)`. Huete, A. R. (1988). *A soil-adjusted vegetation index (SAVI)*. Remote Sensing of Environment, 25(3), 295-309. DOI: [10.1016/0034-4257(88)90106-X](https://doi.org/10.1016/0034-4257(88)90106-X). O padrao do complemento e `L = 0,5`, ajustavel de 0 a 1.
- **EVI2**: `2,5 × (NIR - Vermelho) / (NIR + 2,4 × Vermelho + 1)`. Jiang, Z.; Huete, A. R.; Didan, K.; Miura, T. (2008). *Development of a two-band enhanced vegetation index without a blue band*. Remote Sensing of Environment, 112, 3833-3845. DOI: [10.1016/j.rse.2008.06.006](https://doi.org/10.1016/j.rse.2008.06.006). Implementado apenas para Sentinel-2 L2A e Landsat Level-2 com reflectancia de superficie calibrada; nao equivale a classe ecodinamica.
- **Composições RGB específicas**: RGB natural (Vermelho, Verde, Azul); vegetação em falsa cor (NIR, Vermelho, Verde); urbano em falsa cor (SWIR1, NIR, Vermelho). São produtos de interpretação visual, não índices biofísicos.
- **LST Landsat**: `temperatura em °C = DN × 0,00341802 + 149 - 273,15`, para a banda ST do Landsat Collection 2 Level-2. Consulte [USGS — Scale Factors](https://www.usgs.gov/faqs/how-do-i-use-a-scale-factor-landsat-level-2-science-products) e [USGS — Landsat Surface Temperature](https://www.usgs.gov/landsat-missions/landsat-surface-temperature).

## Fusao termica-optica (versao 2.2.0)

**Agam, N.; Kustas, W. P.; Anderson, M. C.; Li, F.; Neale, C. M. U. (2007).** *A vegetation index based technique for spatial sharpening of thermal imagery*. Remote Sensing of Environment, 107(4), 545-558. [DOI 10.1016/j.rse.2006.10.006](https://doi.org/10.1016/j.rse.2006.10.006); [PDF disponibilizado pela autora](https://www.nuritagam.com/Papers/2006-Agam-et-al-RSE-TsHARP.pdf), p. 2-4 do PDF (p. 546-548 do artigo). Consultado em 01/10/2026.

O TsHARP relaciona temperatura e vegetacao na escala agregada e redistribui detalhes com correcao de residuos. O complemento adapta a base `1-(1-NDVI)^0,625`, conserva media de Kelvin^4 onde existe suporte (emissividade constante), trabalha por cena/data e exclui agua/NDVI invalido. A grade final de 30 m e estimada, nao resolucao termica observada.

Holdout espacial, minimo de 40 celulas, R2 minimo 0,2, recusa de relacao nao inversa, limite da amplitude NDVI e mascara de estimativas sao salvaguardas desta implementacao, nao criterios universais estabelecidos pelo artigo. Preenchimento opcional de lacunas e regressao sem temperatura observada nesses pixels; recebe codigo separado. R2/RMSE internos nao substituem validacao independente fina, especialmente em areas urbanas. Metodo e parametros sao registrados em JSON. Bandas/LST originais permanecem intactas.

## Controle de qualidade por pixel

- Sentinel-2: reamostragem por vizinho mais próximo da SCL; o complemento conserva somente SCL 4 (vegetação), 5 (solo exposto) e 6 (água). A classificação decorre do Sen2Cor: [ESA/Sen2Cor L2A ATBD](https://step.esa.int/thirdparties/sen2cor/2.10.0/docs/S2-PDGS-MPC-L2A-ATBD-V2.10.0.pdf).
- Landsat: `QA_PIXEL` é uma máscara por bits; preenchimento, nuvem, cirrus, sombra e neve se tornam NoData. Referência: [USGS — Landsat Collection 2 QA bands](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands).

## Acervo local consultado

- `Relatorio_Classificacao_Camadas_Indices_Riacho_do_Mel.pdf`, p. 3: NDVI com Sentinel-2 L2A B08/B04 e manutenção de SCL 4/5/6; p. 7: uso de reflectância de superfície e referência a Rouse/ESA.
- `Relatorio_Academico_Riacho_do_Mel_Topicos_Especiais.pdf`, p. 9: Esri RGB é contextual e não possui NIR para NDVI; p. 15: referência a Rouse e limites de interpretação.
- `Viana_2011_Sensoriamento remoto termal aplicado à caracterização de feições cársticas na região de Iraquara-BA.pdf`, p. 1: contexto de aplicação de sensoriamento termal na região. A conversão LST implementada vem da documentação USGS acima.
- `97499.pdf`: Rambo, E. M. et al. **Processamento e classificação de imagens Landsat-8 para uso e ocupação do município de Tupãssi-PR utilizando o Google Earth Engine**, p. 1–3. O estudo usa múltiplas datas, máscara de nuvens/sombras e mediana antes da classificação. O complemento adota QA por cena, mas usa mosaico de prioridade para preencher SLC-off, sem mediana; são operações relacionadas, porém não equivalentes.
- `CONEAGRI_ModeloDeProcessamentoLandsat8_2023_v2.pdf`: Silva, T. A.; Amorim, F. R. **Desenvolvimento de modelo de processamento de imagens Landsat 8 no software QGIS**, p. 1 e 6–7. Referência para organizar o fluxo QGIS de mesclagem/mosaico, reprojeção e recorte; não é uma solução específica para SLC-off.
- `composiciones_landsat_en-arcgis.pdf`: Franco, R. **Composiciones Landsat en ARCGIS**, p. 10 (bandas e resolução), p. 17 (combinações RGB) e p. 28 (linhas incompletas do Landsat 7 após maio de 2003). O guia indica RGB natural 3-2-1 para Landsat 7.
- `LDCM-L8.R1.pdf`: **Productos LDCM - Landsat 8**, p. 26–28, descreve historicamente a banda de qualidade BQA de Landsat 8 e sua codificação binária. É material anterior ao Collection 2; por isso não é usado para decodificar os bits do `QA_PIXEL` atual nem para substituir escalas/offsets de Collection 2.
- `nathalia_costa,+3+Processamento+Digital+de+imagens+multitemporais.pdf`: Shimabukuro, Y. E.; Almeida-Filho, R. **Processamento Digital de Imagens Multitemporais Landsat-5 TM e JERS-1 SAR Aplicado ao Mapeamento e Monitoramento de Áreas de Alteração Antrópica na Amazônia**, p. 5–6. A retificação radiométrica e o corregistro entre datas são necessários em estudos comparativos; o artigo não descreve gap filling SLC-off.

## Limites de interpretação

Índices dependem de cena, época, calibração, resolução, máscara de qualidade e cobertura real. Para comparação temporal, mantenha sensor, nível de processamento, parâmetros e critérios de máscara consistentes. O preenchimento SLC-off por cenas de outras datas pode introduzir variação sazonal; não equivale a recuperar a observação original. As composições RGB e os basemaps Esri, Google e Bing servem para inspeção visual, não para estimar índices quantitativos.

## Classificacao numerica dos produtos (versao 2.1.0)

**QGIS Development Team.** Documentacao e implementacoes nativas de classificacao: [Graduated Renderer](https://docs.qgis.org/3.44/en/docs/user_manual/working_with_vector/vector_properties.html#graduated-renderer), [Raster Color Ramp Shader](https://docs.qgis.org/3.44/en/docs/user_manual/working_with_raster/raster_properties.html#color-ramp-shader-classification), [codigo de Jenks](https://github.com/qgis/QGIS/blob/master/src/core/classification/qgsclassificationjenks.cpp) e [codigo de desvio-padrao](https://github.com/qgis/QGIS/blob/master/src/core/classification/qgsclassificationstandarddeviation.cpp). Consultados em 30/09/2026; validacao local no QGIS 4.2.

O complemento reutiliza `QgsClassificationEqualInterval`, `QgsClassificationQuantile`, `QgsClassificationJenks` e `QgsClassificationStandardDeviation`, aplicados aos valores de indice/temperatura, e representa as classes com `QgsSingleBandPseudoColorRenderer` discreto. Limites manuais sao intervalos numericos definidos pelo usuario, com extremos abertos e inclusao do limite superior na classe inferior.

Intervalos iguais usam extremos exatos; quantis/desvio-padrao usam ate 10000 pixels em amostragem sistematica e Jenks ate 1000. NoData e nao finitos sao excluidos. Empates, amostragem e distribuicoes constantes podem reduzir classes; o algoritmo de desvio-padrao tambem ajusta a quantidade. Os limites efetivos sao salvos no QML para auditoria. Detalhes operacionais: secao Classificacao automatica de `README_imagens_satellite.md`.

Rouse, McFeeters, Zha/Gao/Ni e Huete fundamentam as **equacoes dos indices**, nao limiares universais de classificacao. Os PDFs do acervo acima permanecem referencias das etapas anteriormente registradas; nenhum novo limiar universal foi inferido deles. Nao confundir classes numericas com classificacao supervisionada de cobertura da terra. Compare datas com limites fixos e amostras de referencia; limiares de SAVI nao sao automaticamente os mesmos de NDVI. LST e temperatura em Celsius, nao diagnostico automatico de ilha de calor.

