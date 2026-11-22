# What the aggregation can see, and what it cannot

The simulator does not record users or single orders. `facts_hourly` has one row per hour and per segment
(country x platform x category x channel), with the counts of all users in that segment added up. Which columns and
dimensions exist decides which problems the monitor can find and where it can place them.

## What we chose

| Choice | Value | What it allows |
|---|---|---|
| Time | one row per hour | A fault that lasts a few hours shows up; one that lasts 20 minutes does not |
| Dimensions | country (4), platform (3), category (5), channel (3) = 180 segments | A fault can be placed in a country, a platform, a category or a channel, or a mix |
| Measures | the purchase funnel (sessions to orders), money, refunds, ads, stockouts | A drop can be traced to the step where it happens |

## What it cannot see

- A fault that exists only in a dimension we do not have (a payment method, an app version, a device model). The
  monitor would see "Germany on Android got worse" and could not say more.
- Why one particular order failed. There are no single orders.
- Anything shorter than an hour.

## The trade-off: fine segments are noisy

Measured on the default 28-day database (120,960 rows):

| Level | Typical volume | Consequence |
|---|---|---|
| One segment-hour (the row level) | 91% of rows have fewer than 5 payment attempts; 41% have no order at all | A single row's payment success rate swings between 0.0 and 1.0 by chance. Alarming on one row would be wrong all the time |
| Country x platform, per hour | Germany on Android: about 19 payment attempts at night, about 118 at the evening peak | Visible by day, hard to call at night |
| One country, per hour | Germany: about 249 orders per hour at the peak | Stable, but a fault in one platform is diluted |
| Everything, per hour | About 629 orders per hour at the peak | Very stable, but a fault in one country and platform moves it only a little |

So the monitor has to look at a level where the numbers are large enough to tell a real change from chance, then
drill down only into segments that still have enough volume. The data page marks a row red only when it has at least 5
attempts, for the same reason.

## Consequences for the demo

- The faults we inject (payment failure by country and platform, stockouts by category, search regression and ad budget exhaustion by country, promo by country, late data by source) can all be
  expressed in these dimensions. A fault by payment method could not be injected without adding a column.
- Changing a column or a dimension changes the data contract; it has to be changed in `sim/`, `docs/contracts/` and
  `brain/metrics/` together.
