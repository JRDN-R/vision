# Optional server updates for the iOS Live Activity

Vision's free Xcode build can display a Live Activity while the app has execution
time. The app's background URLSession transfer is independent of that display.
The optional configuration below lets FUPCJ Server send Vortex preparation
progress through Apple Push Notification service after iOS suspends the app.

This requires a paid Apple Developer membership, an app identifier with Push
Notifications enabled, a matching signed iOS build, and an APNs authentication
key. It does not create a Live Activity from the share extension. Open the
download in Vision once to start its activity. Push-to-start is not implemented.
Apple may delay or discard push updates; the display marks stale data and must
not be treated as proof that a file is already in Photos.

## FUPCJ Server setup

1. Update FUPCJ Server using the matching `Setup-Vision-PC.ps1 -Action Update`.
2. Create an APNs key in your Apple Developer account. Keep the `.p8` private key
   on FUPCJ Server, outside the repository and web directory. Use an
   administrator/SYSTEM-readable location inside the existing protected
   `C:\ProgramData\VisionPC` installation, for example
   `C:\ProgramData\VisionPC\private\AuthKey_YOURKEYID.p8`.
3. Install the optional HTTP/2 dependency from Administrator PowerShell:

   ```powershell
   $VisionRoot = Join-Path $env:ProgramData 'VisionPC'
   & (Join-Path $VisionRoot 'runtime\python.exe') -m pip install -r (Join-Path $VisionRoot 'requirements-ios.txt')
   ```

4. Add the following object to the existing `config.json`, preserving every
   existing field. Replace the example identifiers with the real identifiers
   from Apple and the exact main app bundle identifier selected by the Mac setup
   script. The widget extension bundle identifier is not the APNs topic.

   ```json
   "iosAPNs": {
     "enabled": true,
     "teamId": "YOURTEAMID",
     "keyId": "YOURKEYID0",
     "keyPath": "C:\\ProgramData\\VisionPC\\private\\AuthKey_YOURKEYID.p8",
     "bundleId": "com.yourname.vision",
     "environment": "sandbox"
   }
   ```

   Use `sandbox` for an Xcode development build and `production` for a
   distribution/TestFlight build. The signed app and server environment must
   match. Key IDs and team IDs must each be exactly ten uppercase letters/digits.

5. Restart with the normal installer commands:

   ```powershell
   $VisionSetup = Join-Path $env:ProgramData 'VisionPC\Setup-Vision-PC.ps1'
   & $VisionSetup -Action Stop
   & $VisionSetup -Action Start
   ```

6. Build the paid/push variant on the Mac. Open Vision on the iPhone, start a
   Vortex download, and allow Live Activities. The authenticated
   `GET /api/ios/capabilities` response reports
   `liveActivityPush.ready: true` once server setup is valid.

Missing/invalid optional push configuration leaves all other Vision services
available. The capability response gives a short setup message without exposing
the private key or its contents.

## What is sent

The app registers `{jobID, recordID, activityID, token}` using its current
Firebase identity. The server verifies ownership of the existing Vortex job;
clients cannot choose an APNs host, app topic, team, key or environment. Up to ten
activity tokens per account are retained for at most eight hours, encrypted with
Windows DPAPI. Account deletion removes these records.

The worker checks existing Vortex job progress every five seconds and sends
changed progress at most once every fifteen seconds. Unchanged active progress
is refreshed at most once a minute. End/error events can bypass that interval.
APNs receives only a bounded title, phase, progress, status and update timestamp.
No Vision projects, Firebase credentials, media bytes or Google credentials are
included. Apple necessarily receives the destination ActivityKit token.

When FUPCJ Server finishes the media, the message is **Ready for iPhone**. It does
not claim **Saved to Photos** or **Saved to Files**. The iPhone handles the actual
transfer and final save and updates/ends the Live Activity locally. A failed or
cancelled Vortex job ends its activity. No push-to-start messages are generated.

## Transfer envelope

The free and paid builds both use `POST /api/ios/vortex/{jobID}/transfer-ticket`
to obtain a 24-hour capability for an owned queued/processing/completed download.
The returned URL contains no credential. The client supplies the capability in
`X-Vision-Transfer`; the server stores only its hash. One account can have one
active stream, with two streams globally, leaving six of the eight HTTP workers
available for other work.

`GET /api/ios/vortex/{jobID}/transfer` returns this binary protocol:

1. Exact ASCII magic `VTX1\n`.
2. Bounded newline JSON `progress` frames while the server prepares media, with
   a heartbeat every fifteen seconds.
3. One JSON `file` frame containing `filename`, `mimeType` and byte `size`, then
   exactly that many raw bytes. A JSON `error` frame ends a failed preparation
   without any file frame.

The client validates the framing, limits, filename and exact byte count before
moving the payload into its durable download folder. The envelope itself is
never saved as a movie. There is no HTTP Range resume for this envelope; a retry
starts the transfer again. Waiting is capped at two hours. The server job keeps
running if that wait ends, a network proxy interrupts the response, or iOS stops
the transfer. Reopening Downloads can retry the same job. This is best effort,
not a promise that iOS will run code continuously or finish every transfer while
the app is force-quit.

## References

- [Apple: Starting and updating Live Activities with ActivityKit push notifications](https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications)
- [Apple: Establishing a token-based connection to APNs](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns)
- [Apple: Establishing a connection to APNs](https://developer.apple.com/documentation/usernotifications/establishing-a-connection-to-apns)
- [Google: OpenID Connect API reference](https://developers.google.com/identity/openid-connect/reference)
