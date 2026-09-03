# Licensing status

ClassScribe does not yet have a finalized source license. `inventory.v1.json` records the source as
`NOASSERTION`, denies any assumption of redistribution permission, and makes this a hard release
gate. This notice is not a grant of rights.

Model weights are never bundled in the RPM. A user must review the pinned repository, revision,
download size, hash manifest, license, and any gated access conditions before an explicit install.
The upstream model license continues to govern those separately downloaded files.

The release build must generate complete Python and JavaScript dependency notices/SBOMs from the
frozen lockfiles. The repository inventory is a control record, not legal advice and not a
substitute for the actual license texts.
