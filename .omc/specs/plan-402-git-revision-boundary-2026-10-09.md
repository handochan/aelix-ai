# #402 — git integrity revision boundary

Base main64b82af7/codea0a9c328. Owner confirmed no other session is implementing
#402. Atomic lock .git/codex-issue-locks/402, isolated codex-402 worktree.

Acceptance: the actual URL-path last@ revision is the only 40-hex pin candidate;
fragment/query/authority cannot impersonate it; default acquisition does not
record falsepins; strict/update refuse before installer; actual pin/identity/
subdirectory/name references remain correct. Installer argv unchanged.

Reference: current Pi f1b2e77f5b13b2a199b1052cb79c235451afe7d7 has a Node/npm
package manager, no Python SHA verification to port (ADR0187 is Aelix-owned).
Current pip Git/versioncontrol parsing uses URL-path last@ and unquotes the ref;
git+file path normalization is reparsed and can relocate escaped ?/#. Therefore
encoded ?/# git+file paths conservatively cannot establish a pin. Spaces remain
supported. No new production dependency, network pre-verification or git-object
inspection is added. Existing same-shaped40hexref limitation stays documented.

Run red regressions onoldbehavior, focused CLI/pin/catalog/security tests,
ruff/type/citation/doc gates, actual pip and uv localgit install/update/strict
controls, newcontext independent adversarial review, fullplatformCI.

Fresh review addedtwo necessarysameboundaryrepairs: rawTAB/CR/LF cannotestablishpin (urlsplitotherwisecleanscandidatewhileidentityslicesrawbytes); namedURLextractionusesexistingruntimepackaging.Requirement instead ofregexwhitespacecut. ActuallegalSHA+NBSPbranch withfragment madepipinstallBwhileoldhelperclaimedA/strictverified; canonicalfullURLnowdeclinespin/refusesstrict. ASCIInamedgrammar/scpremainvalid.
