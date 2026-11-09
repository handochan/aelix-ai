# Board moves and follow-up issues

## Moving a board item, by lookup

Project boards in GitHub Projects (v2) address everything by opaque ids. Look them
up each time, because a field or option recreated by someone else gets a new id.

```bash
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)    # <owner>/<repo>
OWNER=${REPO%%/*}
gh project list --owner "$OWNER"                                # the board's number
gh project view <number> --owner "$OWNER" --format json -q .id  # the project id
gh project field-list <number> --owner "$OWNER" --format json \
  -q '.fields[] | select(.name=="Status") | {id, options}'      # field id and option ids
```

Get the issue's item id with GraphQL, not `gh project item-list`. The item list has
omitted the newest items for half an hour or more after they were filed:

```bash
gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){
  issue(number:$n){projectItems(first:10){nodes{id project{number}}}}}}' \
  -f o="$OWNER" -f r="${REPO#*/}" -F n=<issue>
```

Then move it:

```bash
gh project item-edit --project-id <project id> --id <item id> \
  --field-id <Status field id> --single-select-option-id <option id>
```

Notes:
- A brand-new issue may need a few seconds before its item exists. Retry the
  lookup; do not conclude it is absent from one `item-list`.
- `gh project item-add` is not reliably idempotent. It has returned the existing
  item for one issue and failed with "Content already exists in this project" for
  the next. Look the item up first, and add only as a fallback.
- A single-select field's options are replaced as a whole by
  `updateProjectV2Field`. To add one, send every existing option with its `id` plus
  the new one. An option sent without its id is recreated, and its items lose their
  value. Snapshot every item's value before and diff after.
- Many boards close an issue's item to Done automatically. Check after closing, and
  move it yourself if not.

## Follow-up issues and comments

What a narrowed scope leaves open becomes an issue, not a sentence in a handoff.

1. **Search before drafting.** For each item, run
   `gh issue list --state all --search "<words> in:title"`, plus a body search for the
   symbol or test name. A duplicate splits the evidence. Check comment drafts the
   same way: "new" defects listed in a comment have turned out to be open issues.
2. **Draft to a file**, one per issue or comment. Write the title, the observed
   behaviour with the command and output, the cause if measured, the proposed scope,
   and "Done when".
3. **Make every pointer public.** Link a permalink at a pushed SHA, a CI job URL, or
   an issue or PR number. Never link a local path or an untracked file, which a
   public reader cannot open.
4. **Fact-check independently.** A reviewer who did not draft them reads every draft
   and every comment against the tree. The comments are part of this, not an
   afterthought. It checks each claim, number, symbol, ADR citation, origin issue
   and version scope, and writes corrected texts to new files.
5. **Post the checked files**, not the drafts. Drive a loop of `gh issue create` or
   `gh issue comment` calls from Python with argument lists (zsh does not split
   words, and a half-failed loop gives issue numbers to the wrong drafts). Afterwards,
   list what was actually created before you continue.
6. **If something was posted unchecked,** fact-check it at once and post a dated
   correction comment for each error.

### Reading pi's issues

`earendil-works/pi` closes every issue from a new contributor automatically, with
`stateReason: NOT_PLANNED` and a `github-actions` comment. If that comment is the only
one, treat the issue as unreviewed, not rejected, and read its body: it may hold the
best design available. Verify any claim against pi's code at `origin/main`.
