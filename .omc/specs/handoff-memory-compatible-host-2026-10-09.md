# Memory compatible host release

Owner scope: normal installation users must have the Memory /settings row,
including the compatible host release and official catalog update.
GitHub issue418, Project1 In progress. Source baseline is published beta.2
1093cc25; maintenance version 0.1.0b2.post1. Candidate PR targets maintenance/beta.2;
a separate default-main PR changes version metadata, update feed and release policy.
Do not tag until independent review, both PR CI gates, actual PTY and live-memory
verification pass. Both release publication jobs require the exact wheel artifact
matrix on Linux/macOS/Windows x Python 3.11/3.12/3.13. Do not include or close the
unfinished beta.3 milestone exits. Verification record is docs/verification/memory-compatible-maintenance.md.
Original user and parallel-agent checkouts are preserved. Local QA uses temporary
synthetic stores; no credentials, user memory or session transcripts are committed.
