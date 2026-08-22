# Data contract

`scripts/seed_data.py` creates deterministic, fictional data under
`data/generated/`. No real customer, restaurant, HubSpot, GA4, or payment data is
included.

The generated sources deliberately contain a small number of quality failures so
the Data Trust screen has something real to detect:

- duplicate customer emails;
- missing acquisition sources;
- orphan website events;
- an invalid order customer reference;
- one malformed catering lifecycle transition.

The analysis layer excludes invalid financial records from revenue KPIs while
reporting every detected issue separately.
