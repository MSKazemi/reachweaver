# Claude directory disclosure and submission review

**Status: NOT cleared for submission; maintainers must explicitly review and accept all declarations.**  
**Source snapshot reviewed:** public main b915b4175adcceaff41688a0487f9d53628e61fb, 12 October 2026.

## Documentation and manifest
- [Privacy notice](../PRIVACY.md)
- [User responsibility notice](../TERMS.md)
- [Local privacy lifecycle](PRIVACY-LIFECYCLE.md)
- [Security policy](SECURITY.md)
- [Plugin-specific README](../plugin/README.md)
- [Directory Terms](https://support.claude.com/en/articles/13145338-anthropic-software-directory-terms)
- [Directory Policy](https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy)
- [Plugin manifest specification](https://code.claude.com/docs/en/plugins-reference)

## Portal answers requiring confirmation

| Prompt | Recommended truthful description | Status |
|---|---|---|
| Reads/stores personal data | **Yes.** Local drafts and approval labels can identify people, and support reports may include data. | Review |
| Sends data elsewhere | **Yes.** Scheduled GitHub update checks, optional API/identity checks, explicit live publishing and optional configured integrations; external providers also receive network metadata. | Review |
| Retention | **Do not assert 'not retained' without first confirming the portal's definition.** Local DB persists until operator-managed purge; some minimum identifiers remain; providers have separate practices. | Blocker |
| Under 18 | Not designed for under-18 users; no built-in age verification has been claimed. | Review |

## Legal and operational blockers

1. Read all current Anthropic Software Directory Terms and Policy, including any representations, security obligations, third-party privacy disclosures and indemnification. A user-facing disclaimer cannot override the separate Anthropic agreement. Independent qualified legal advice is recommended before personal submission.
2. Validate rights to code, documentation, brand and assets, including any third-party contributions or affiliation claims. Apache licensing is **not** evidence of sole ownership.
3. Re-check the plugin behavior at the new submission SHA: network calls, hook startup, credential handling, data access, persistence, external APIs and any enabled backend. A test suite is not a production audit.
4. Check external provider privacy policies for every enabled integration; do not represent them as being covered by ReachWeaver's own policy.
5. Confirm directory answers, legal entity/submitter authority and privacy contact; do not attest on another person's behalf.
6. Run official `claude plugin validate ./plugin` and the project's release tests; review branch diff; record exact submission commit. Portal validation of an earlier commit is not validation of later documentation changes.
7. Document a route to receive reports and a workable correction/withdrawal process.

**Submitter-only step:** Review and tick declarations in the Anthropic portal yourself once the factual and legal questions are resolved. The repository, maintainers and automation must not treat this document as acceptance.
