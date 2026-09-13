# PDF page fixtures

Word positions (text, x0, x1, top) of the restated financial statement pages of three public offer documents, so
tests exercise table reconstruction, label mapping and reconciliation on real document geometry without shipping
the PDFs (8–13 MB each). `caption` keeps only the page's text lines that mention share values.

| File | Document | Source |
| --- | --- | --- |
| `ola_electric_rhp.json` | Ola Electric Mobility Limited, Red Herring Prospectus dated 26 July 2024 | `cdn.olaelectric.com/sites/evdp/pages/investor/ola_electric_mobility_limited_rhp_unsigned.pdf` |
| `hyundai_motor_india_rhp.json` | Hyundai Motor India Limited, Red Herring Prospectus dated 8 October 2024 | `nsearchives.nseindia.com/content/equities/IPO_RHP_HYUNDAI.pdf` |
| `rentomojo_drhp.json` | Rentomojo Limited, Draft Red Herring Prospectus dated 27 March 2026 | SEBI public issues filings |

Regenerate by placing the PDFs in `data/samples/` and reading the chosen pages with `PdfSource.lines`.
