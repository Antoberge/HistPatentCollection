# Immigration

File| Source
---|---
[immigration_us.csv](https://github.com/cverluise/patentcity/tree/master/assets)| [US census](https://www.census.gov/content/dam/Census/library/working-papers/2006/demo/POP-twps0081.pdf)
[immigrationbyorigin_us.csv](https://github.com/cverluise/patentcity/tree/master/assets)| [US census](https://www.census.gov/content/dam/Census/library/working-papers/2006/demo/POP-twps0081.pdf)

## Coverage

Country| Geographical level| Period
---|---|---
US | 0(country) | 1850-2000

## Variables

=== "immigration"

    Variable|Description    | Type
    ---|---|---
    name    | Name of the origin country/region | `str`
    year    | Year | `int`
    value   | Value | `float`

=== "immigrationbyorigin"

    Variable|Description    | Type
    ---|---|---
    region    | Region | `str`
    level   | Geographical level of the entity defined by `name`. `1`: Countries, `2`: Country groups, `3`:United Kingdom, `4`: Other regions, `5`: European regions, `6`: Continental areas, `7`: Continent, `8`:Subtotals, `9`: Total, `"n.e.c."` Not elsewhere classified  (e.g. Europe)| `int`
    year    | Year | `int`
    immigrants | Number of immigrants (in units) | `float`

    !!! info "Focus"

        - Geographical levels: The table from census.gov proposes different scales. We add a column `level` to specify the geographical level at which a given entry is defined.
        - Wales: Wales unitary authorities are an aggregation of districts made using wikipedia historical data.
