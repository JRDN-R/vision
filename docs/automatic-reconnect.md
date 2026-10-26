# Automatic reconnect

Workspace 1.1.0.5 and native iOS 1.1.1 (build 3).

The selected native workspace automatically reloads after WebKit process
termination, navigation failure, timeout or retryable HTTP responses. Retries
back off from 1 second to at most 30 seconds, pause while the app is inactive
or the other workspace is selected, and resume when visible again. A healthy
page is not reloaded. Existing account storage and on-device project recovery
copies are retained. Edits that never reached any storage before iOS killed
the process cannot be recovered by reloading.

The web project connection retries every 15 seconds while visible when a save
is pending or a read reported the server offline. Online/foreground events
also trigger a check. Retries invalidate cached server health, do not overlap,
and respect account ownership, temporary sessions and revision conflicts.
Pending edits use the existing save/revision path; saved boards reconcile
through the existing remote-read path. No whole-page refresh is used for a
server outage.

After merge/publication, run the existing Mac iOS updater with the connected
phone to install native 1.1.1. No PC server update is required for this change.
The web changes arrive through the normal workspace cache update.

Tests cover saved-but-offline recovery, pending edits, conflicts, hidden pages
and overlapping attempts. Native simulator tests cover deferred recovery and
cancellation on backgrounding. A physical-device network interruption and
return-from-background check remains useful after installation.
