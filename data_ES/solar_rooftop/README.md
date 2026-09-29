# Rooftop PV capacity per person by NUTS2 region

File: `rooftop_pv_kw_per_person_nuts2.csv`

## Objective

PyPSA-Eur sets the rooftop PV potential with a single value for all regions: 2 kW/person
(0.1 kW/m² × 20 m²/person, in `scripts/build_solar_rooftop_potentials.py`). This dataset replaces
that value with estimates for each Spanish autonomous community (NUTS2). The estimates come from
the optimal self-consumption installations computed in Gallego-Castillo et al. (2021).

## Method

The estimates start from the average residential building in each region, as defined in the
paper (Table 2, from the 2011 Population and Housing Census):

- the number of households per building (`households_per_building`);
- the roof (terrace) surface of the building (`roof_area_m2`).

For this building, the paper gives the optimal PV capacity per household and the share of the
roof it occupies (Table 5). Capacity per household is converted to capacity per person by
dividing by the average household size of each region (INE).

Three scenarios are provided:

| Scenario | Column | Description |
|---|---|---|
| Optimal, no surplus remuneration | `kw_per_person_opt_no_surplus_remuneration` | Optimal installation when surplus energy fed into the grid is not remunerated. Sized to cover on-site consumption only. |
| Optimal, with surplus remuneration | `kw_per_person_opt_surplus_remuneration` | Optimal installation when surplus energy is remunerated under the Spanish simplified compensation scheme (RD 244/2019). |
| Full roof use | `kw_per_person_full_roof` | PV covering 100 % of the average building roof. Computed as optimal capacity ÷ roof share, averaging both remuneration cases (they agree within 0.7 %). **Not a realistic scenario**; it is a physical upper bound. |

Values are given for 17 regions and for Spain as a whole (`ES`).

## Sources

- Gallego-Castillo, C., Heleno, M., Victoria, M. (2021). *Self-consumption for energy
  communities in Spain: A regional analysis under the new legal framework*. Energy Policy 150,
  112144. https://doi.org/10.1016/j.enpol.2021.112144 (preprint: arXiv:2006.06459).
- INE, Estadística Continua de Población, table 60132 "Tamaño medio de los hogares de personas
  residentes en viviendas familiares por fecha" (average household size), value for
  1 July 2026, provisional (https://www.ine.es/jaxiT3/Tabla.htm?t=60132).

## Caveats

- **Consistency between Tables 3 and 5.** With the 10 m²/kW occupation factor, the roof shares
  reported in Table 5 correspond to a roof area of about 1.43 × `roof_area_m2` in every region
  (equivalently, about 7 m² per kW). The full-roof values are derived from the reported roof
  shares. If they were recomputed as `roof_area_m2` / 10 m²/kW instead, they would be about 30 %
  lower.
- **Full-roof values.** They do not deduct shading, obstacles (lifts, antennas), orientation or
  structural limits. The paper notes that roof shares above about 50 % are unlikely in practice.
- **Residential buildings only.** Commercial and industrial roofs are excluded.
- **Mixed reference years.** Building data are from 2011; household size is from 2026.
- **Ceuta and Melilla (ES63, ES64)** are not covered by the paper.
