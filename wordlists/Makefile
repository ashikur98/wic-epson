# SPDX-License-Identifier: AGPL-3.0-or-later
.SUFFIXES:
MAKEFLAGS += --no-builtin-rules
.SECONDARY:
.DELETE_ON_ERROR:

wikidata-taxons.csv.alnum.words.Keys8:
Adjprog.exe.graph8:


%.hex1: %
	xxd -g 1 -c 1 $< |cut -d ' ' -f 2 |tr '\n' ' ' >$@
%.hex: %
	hexdump -C $< >$@
%.sorted: %
	sort -u $< >$@
%.counts: %
	sort $< |uniq -c | sort -bnr >$@
%.alnum: %
	tr -cd '[:alnum:] \n' <$< |sort -u >$@
%.words: %
	tr ' ' '\n' <$< | sort -u >$@
%.graph8: %
	grep -oUaE "([[:graph:]]{8})" $< |sort -u >$@
%.Keys8: %
	python3 to_keys_epson.py <$< >$@


define WD_RQ :=
SELECT DISTINCT ?name
WHERE
{
  ?item wdt:P31 wd:Q2934 ; # a taxon
  wdt:P225 ?name . # taxon scientific/correct name
}
# LIMIT 20
endef

wikidata-taxons.csv:
	curl -L -o $@ --header "Accept:text/csv" "https://query.wikidata.org/sparql?query=SELECT%20DISTINCT%20%3Fname%0AWHERE%20%0A%7B%0A%20%20%3Fitem%20wdt%3AP31%20wd%3AQ16521%20%3B%0A%20%20wdt%3AP225%20%3Fname%20.%0A%7D"
# --data-urlencode query@list-taxons.rq \
# --header "Accept: text/tab-separated-values"


# coldb.zip:
# 	wget "https://download.catalogueoflife.org/col/annual/2021_coldp.zip"

# wfo:=WFOTaxonomicBackbone_v.1.7_20220712
# $(wfo).zip: # https://wfoplantlist.org/plant-list/classifications
# 	curl -L -o $@ "http://104.198.143.165/files/WFO_Backbone/_WFOCompleteBackbone/archive/$@"
# $(wfo)/scientificName.tsv: $(wfo)/classification.txt
# 	cut -f 4 $< | sort >$@
