# Brand and name protection — status and open actions

State of the "Aelix" name as of **2026-07-31**, and what is left to do. This is
research, not legal advice; the filing decisions belong with a trademark
attorney. It exists so the facts are written down once instead of being
re-derived.

## Where the name stands

**Nothing is registered anywhere.** Aelix holds no trademark in any
jurisdiction. The claim rests on use, which began **2026-05-16** when this
repository was first published. That matters more than it sounds: US case law
(*Planetary Motion v. Techsplosion*, 11th Cir. 2001) holds that distributing
free open-source software **is** use in commerce and does create common-law
rights, so the project is not rightless — it is just unregistered.

### The register, checked directly (TMview / EUIPO / CIPO, 2026-07-31)

| Office | AELIX in software classes | Note |
| --- | --- | --- |
| **Korea** | none | 0 records, any class. First-to-file — use gives no priority here |
| **Japan** | none | 0 records |
| **Canada** | none | only `Kaelix` (cl 9) and `FÆLIX` (cl 33) |
| **US** | none in class 9 | one live mark: Aelix Therapeutics, **class 42 limited to HIV vaccine R&D** |
| **EU** | none in class 9 | EUTM 014993885, same owner, class 42 also HIV-pharma-only |
| **UK** | **application pending in 9/35/38/42** | see below |

### The one live conflict

```
AELIX · GB · UK00004414917 · filed 2026-07-13 · status Filed
applicant: INSPIREREALM LIMITED  (Companies House 15401602,
           incorporated 2024-01-10, Bilston, England)
classes:   9, 35, 38, 42
```

Classes 9 and 42 are downloadable software and software design/SaaS — this
project's exact category. It is an *application*, not a registration.
Verified against TMview and Companies House; the goods/services wording itself
could not be read because the UK IPO search is behind a captcha.

### Others using the word (none in this field, all noted for completeness)

`aelix.ai` — AI consultancy, Toronto (no registration anywhere; commercial use
only since ~Feb 2026) · `aelix.net` — Aelix LLC, natural-gas marketing, Maryland
· `aelix.xyz` — a crypto trading agent, live since 2026-07-21 ·
`github.com/NikhilRaikwar/Aelix` — an MIT AI agent on Monad · `aelix.org` /
`github.com/aelix` — a German PHP framework, dormant since 2017 · `aelix.dev` —
a personal blog · `aelix.com` — parked in GoDaddy's for-sale portfolio.

The field is crowded and getting more so. That cuts both ways: it weakens
anyone's claim to exclusivity over the bare word, including ours.

## Dates that are running

| Date | What |
| --- | --- |
| **2026-09-24** | UK opposition period for UK00004414917 closes *(reported, not independently verified — confirm on the UK IPO case page)* |
| **2026-11-29** | Aelix Therapeutics' US §71 10-year declaration is due; +6 months grace to ~2027-05-29. If unfiled, the last live US AELIX mark lapses |
| **2027-01-13** | Paris Convention priority window from the UK filing closes — until then InspireRealm can file elsewhere claiming 2026-07-13 |

## Actions

### Done in this branch

- [x] `TRADEMARK.md` — brand policy. States plainly that the mark is
      **unregistered**, so `Aelix™` and never `Aelix®`.
- [x] `scripts/reserve_pypi_names.py` — builds placeholder distributions for
      `aelix`, `aelix-ai`, `aelix-agent-core`, `aelix-coding-agent`,
      `aelix-server`. All five names are still free.
- [x] `scripts/reserve_npm_names.py` — optional defensive npm placeholders.

### Needs the owner's credentials or an account action

- [ ] **Upload the PyPI placeholders.** Needs a PyPI API token. Trusted
      Publishing cannot do this: PyPI's own docs say a pending publisher
      *"does not create a project or reserve a project's name until it is
      actually used to publish"*. Configuring one and stopping there leaves
      every name open.

      ```bash
      python scripts/reserve_pypi_names.py
      uv publish --token pypi-XXXX dist-reservation/*
      ```

      Then add the GitHub Actions trusted publisher to each new project so
      `release.yml` keeps working without a token.

- [ ] **Claim the GitHub organisation `aelix-ai`** (available). There is no API
      for creating an organisation — it is a web-UI action.
      `aelix` itself is held by a German PHP-framework org, dormant since 2017;
      a GitHub name-release request is possible but not assured.

- [ ] **Register the remaining domains**, all still free:
      `aelix.tools`, `aelix.run`, `getaelix.com`, `aelixhq.com`.
      `aelix.ai` is gone permanently; `aelix.com` is purchasable at a premium
      through GoDaddy's Afternic listing.

- [ ] *(optional)* Publish the npm placeholders — read the caveat in the
      script's docstring first.

### The decision that is not mine

- [ ] **File in Korea, classes 9 and 42.** Korea is first-to-file with no use
      requirement, the register is empty, and it is the home market. Official
      fees are roughly **₩506,000** for two classes (52,000/class filing +
      201,000/class registration); expedited examination adds ~₩160,000 and
      cuts the wait to about three months against a normal 10–14.

      Other jurisdictions, for comparison: US **$350/class** (a Korea-domiciled
      applicant *must* use a US attorney — mandatory since 2019-08-03; the
      intent-to-use §1(b) route works for a product not yet in commerce);
      EU **€850 first class + €50 second**.

## Reviewing this file

Re-check the register before any brand-affecting decision, and update the table.
Facts here were true on 2026-07-31 and trademark registers move.
