# Navigation and Board Controls

SAM Radar keeps the primary report controls in a sticky top command bar so capture operators can move between list, board, follow-up, search, refresh, theme, and editing controls while scanning long reports.

The Board view supports lane visibility preferences for focused reviews:

- All: show every workflow status lane.
- Active Only: show Reviewing, Pursue, Teaming, and Submitted.
- Hide empty lanes: suppress lanes that have no currently visible cards.
- Reset: restore all lanes and turn off empty-lane hiding.

Lane preferences are stored in browser localStorage under `samRadarLanePrefs`. The back-to-top control appears after the user scrolls into the report and hides while the detail modal is open.

Responsive checks for this surface should include 390px, 768px, 1440px, and 1728px. Verify the command bar remains reachable, the lane popover fits, board overflow stays inside the board view, and the report body does not gain horizontal scroll.
