# Weekly report reconciliation

The weekly report counts one reconstructed inbound candidate call for the
configured Membership or Certification hunt group. It includes every normalized
outcome: confirmed answers, forwarded/routing-only calls, voicemail, unanswered,
unknown, ambiguous, and unattributed calls.

The legacy Nextiva crosstabs for September 10–16 and September 17–23, 2026 use
`Count of Union` split by Originating and Terminating direction. Their `Hops`
rows are routing attempts, not calls. Do not add Hops to the weekly-call total.

The two reports can therefore differ without either being corrupted: the weekly
report joins related routing segments into one call candidate, retains
routing-only and ambiguous candidates, and is call-level rather than directional.
Compare each day and department first, then use the candidate outcome table to
explain a remaining difference. Do not change the call-level calculation solely
to force it to equal legacy directional totals.
