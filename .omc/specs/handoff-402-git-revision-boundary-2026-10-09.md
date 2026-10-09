# Handoff — #402 git revision integrity boundary (2026-10-09)

Base main64b82af7/codea0a9c328; isolatedcodex-402, atomiclock402. Owner confirmed no other session isworking. Scope onlyCLIextension integrity parsing/identity and documentedgrammar; no newproductiondeps or git-object fetch/inspection.

Implementation: candidate is URLpathlast@40literalhex; query/fragment/authoritynotpin. Pinidentity removesactualrevision only andretainsURL/query/fragment/subdirectory. Namedrefs useexistingpackaging.Requirement fullURL, preservingUnicodewhitespace inrealref; scpshorthand exemptionretained. RawURLTAB/CR/LFdeclinepin. FileURLescaped?#declinepinbecausepipdecode/reparsecanchangeinstalledref;ordinaryencodedspaces supported.

Independent stages and repairs:
- Firstreview289tests+15gate/288parserinputs/16actualpip26.2.1+uv0.12.23installs clean inoriginalscope; notfinalapproval.
- Freshno-author-contextreview foundP2rawcontrols madeidentitydependonSHA;realA→Bwithtwotabs gotnewTOFIpins instead ofdriftrefusal. AlsoP1namedSHA+NBSPbranch/fragmentmadepipinstallBwhileoldregexclaimedA,andstrictrepeatedfalseverified. ActualPEP610commits confirmed;bothfixed.
- Finalfrozenfreshrecheck1176passed1skipped,21canonical/sibling/errorchecks,actual74backend+NBSP/ASCIIgrammarprobes;candidatecontrol/NBSPnone,defaultnoPIN/verifiedclaim,strict2/calls0,normaladminpins/PEP/scp/markers preserved. Hashf128b88006f0e82386389c8f93cbfe0ef45d737e.
- FreshClaudeSonnet finalsource-onlycrossreview: no remaining concretebug incheckedscope; readpip/sourcecontracts/callers/legacyhandling, didnotexecutecode. InitialOpusproducednojudgmentduetooloutputfilter, explicitlynotapproval; a firstSonnetpass independently foundcontrolidentitybug, finalpassreviewedfix.
- Rootcurrentfocused1129passed21skipped;lintclean,type287files0errors/spike retained,citations951clean.
- Rootactualpip+uvlocalgitlive: mainBthenC installs/updates unpinned,no pins,strictrefuse2;trueAcommitpositivecontrol installsAandstrictverifies0. Dedicatednewvenvs/agent/settings,cwdlocalrepo,noauthkeyorownerstateusage.

Live/reproscript committed .omc/specs/git-revision-402-live.py;rawlogs .omc/probes/402-live. Freshreviewcounterfactuals/archive in codex-fresh-review-402/.omc/specs/. Finalcrossreview JSON .omc/probes/402-cross-review-final/.

Next: rebase ontoordered#420predecessor, actualcitationanchorcheck/fix/check,range-diff,newplatformCI/fullintegration. Merging isownercontrolled underappliedissue-to-PRskill. Noowner~/.aelix/auth change; no keyprinted. NativeWindows,remoteSSH/liveprovider/fullsuite notrun in thisissuepass. Backend-specificconservative refusal andexisting40hexnamedbranch/objecttypev1limitationareexplicit;do notclaimbyteprovenance orautomaticlegacy-pinmigration.

## Final predecessor gates

Rebasedonto#420predecessor24c57ef1: productinstaller/test content unchanged fromreviewed1384dc31;range-diff onlyCHANGELOG/indexcontext. Finalcombined489focusedtests passed2.07s, Ruffclean,type288files0errors/inverse spike retained,citations934none drifted. Nextfullcombinedsuite/platformCI onpublishedhead. RootdirectTUI wasperformed on#420samepredecessor;thisCLIintegritychange hasactualpip/uvinstallation evidence ratherthanproviderchanges.

Finalparent43e436b1 incorporatesENOTDIRedgeandtrustedmissingpromptattempt testcompatibility. Firstcombinedsuite(initial3f697parent24)15389passed25skipped1failedonlyoldtrustassertion,666.48sexit1;itwasnotreportedgreen. Correctedassertpreservessettingsread/knownresourceallowlist/denialzeroattempts. Rootnewparentfocused128tests pass1.22s;candidateinstaller/regressionsbyteidenticalacrossrebase,citations934clean. Finalnewfullsuiteisstartedafterpublication,remoteCIqueued.
