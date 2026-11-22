# E2: interface stability (Astronomy Shop)

**Question:** will the shop's interfaces our adapters depend on (gRPC `demo.proto`, frontend `pages/api/*`) stay stable across upstream releases?
**Method:** sha256 of each file at the running release (tag `3.1.0`, commit dedc017) and at `main` (7a88536, 20 Nov 2026). Raw hashes are in `E2_hashes.txt`.

| File | 3.1.0 → main |
|---|---|
| `pb/demo.proto` (all gRPC contracts) | **identical** |
| `pages/api/checkout.ts`, `products/*`, `recommendations.ts`, `data.ts` | **identical** |
| `pages/api/cart.ts`, `currency.ts`, `shipping.ts` | one line each: `res.status(405)` → `res.status(405).send('')` (wrong-method replies only) |

**Result:** stable. The only drift is how wrong HTTP methods are answered, which our driver never uses.
**Decision:** pin images to `3.1.0` and keep the hash list as a contract test. On any upstream bump, rerun the hashes; a changed `demo.proto` or `checkout.ts` blocks the bump until the adapters are re-checked.
