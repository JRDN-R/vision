# Venture Qwen Ultra Overdrive: MacBook Pro + FUPCJ

**Status:** Optional integration for physical-machine acceptance testing; not yet deployed or a verified speedup. The existing Qwen Ultra, Boost, contexts, files and conversations remain available.

## Hardware and automatic routing

- FUPCJ host: Ryzen 9 5950X, 48GB RAM, Radeon RX 5700 XT with 8GB VRAM. Windows owns Qwen Ultra 27B Q4_K_M, queued jobs, conversation and response.
- Mac worker: MacBookPro18,3 (2021 14-inch), M1 Pro 8 CPU cores (6P/2E), 14 Metal GPU cores, 16GB unified memory, 200GB/s bandwidth.
- Begin with an **8GiB** Mac memory target. Test 9GiB/10GiB only after measuring memory pressure and swap. A GPU device tensor split and monitored worker RSS are **not** a hard RAM limit; Metal may allocate outside RSS.
- For **every new Qwen Ultra response**, FUPCJ performs a short authenticated-tunnel health request and checks that the forwarded RPC socket is listening. Online, healthy, separately benchmarked => distribute. Offline, sleeping, unavailable, not tuned, or OBS/Streamlabs active => preserve original FUPCJ-only profile.
- If the Mac sleeps between the two checks, the runtime reverts to the same local Ultra model **before starting generation**. If the Mac disappears *mid-response*, retain the partial answer and report a failure rather than silently regenerate and duplicate content.
- The Mac's one-minute sleep settings mean it will often be unavailable on battery. The helper never secretly alters sleep settings. Use AC for long work; opting into a keep-awake mode is an operator decision.

## Security boundary

The official [llama.cpp RPC documentation](https://github.com/ggml-org/llama.cpp/blob/f2cc7282ce0042641df55e0e563b75e824e2b647/tools/rpc/README.md) explicitly calls the protocol experimental and unsafe on open networks.

**NEVER expose RPC or health to a LAN, tailnet or the public internet.** The Mac RPC service and health responder bind **only to 127.0.0.1**. Use two **authenticated SSH port forwards over Tailscale**, with the Mac SSH host key verified, a restricted user/key and appropriate Tailscale ACLs. The worker ID is an identity check, not authentication. Windows accepts no remote addresses from browser settings, prompts, project files or account data.

## 1. Build and start the Mac worker

Install Tailscale on both devices, enable Remote Login for a designated Mac SSH user, and confirm the Windows-to-Mac SSH host fingerprint. Install Apple's Command Line Tools, Git and CMake as necessary.

Build the exact pinned llama.cpp revision with Metal and RPC:

~~~bash
git clone https://github.com/ggml-org/llama.cpp.git "$HOME/llama-overdrive-src"
cd "$HOME/llama-overdrive-src"
git checkout f2cc7282ce0042641df55e0e563b75e824e2b647
cmake -S . -B build-overdrive -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DGGML_RPC=ON
cmake --build build-overdrive --config Release --target ggml-rpc-server -j 4
~~~

Download the reviewed root-level **mac-overdrive-worker.py** from this Venture revision. Run:

~~~bash
python3 mac-overdrive-worker.py \
  --rpc-binary "$HOME/llama-overdrive-src/build-overdrive/bin/ggml-rpc-server" \
  --rpc-port 50052 --health-port 50053 --budget-gib 8
~~~

The companion starts RPC only on localhost, checks the actual process/socket, and serves a localhost-only JSON health endpoint. It prints a **32-character Mac worker ID** for Windows configuration. Verify the RPC log actually lists a **Metal** device; a CPU-only RPC build is not an acceptable GPU test. A login LaunchAgent can be installed after validation; default sleep/battery behavior is unchanged.

## 2. Create two Windows localhost forwards

With Tailscale running on both systems, in Windows PowerShell:

~~~powershell
ssh.exe -N \
  -o ExitOnForwardFailure=yes \
  -o StrictHostKeyChecking=yes \
  -o ServerAliveInterval=10 \
  -o ServerAliveCountMax=2 \
  -L 127.0.0.1:51052:127.0.0.1:50052 \
  -L 127.0.0.1:51053:127.0.0.1:50053 \
  macOS-user@mac-tailscale-address
~~~

For PowerShell, run the command on a single line or replace each trailing backslash with a PowerShell backtick. These forwards carry:
- Windows localhost 51052 => Mac localhost RPC 50052
- Windows localhost 51053 => Mac localhost health 50053

Neither RPC nor health is directly reachable outside its own device. Keeping the tunnel connected is optional: FUPCJ works normally when the tunnel is absent.

## 3. Build and register a separate Windows Vulkan + RPC runtime

Do NOT replace FUPCJ's pinned, SHA-verified Qwen Ultra runtime. The existing Vulkan binary might lack RPC support.

With Visual Studio C++ Build Tools, CMake and Vulkan SDK on the Windows PC:

~~~powershell
git clone https://github.com/ggml-org/llama.cpp.git llama-overdrive-src
cd llama-overdrive-src
git checkout f2cc7282ce0042641df55e0e563b75e824e2b647
cmake -S . -B build-overdrive -DGGML_RPC=ON -DGGML_VULKAN=ON
cmake --build build-overdrive --config Release --target llama-server --parallel 8
~~~

Locate the output directory containing **llama-server.exe** and its related **.dll** libraries (often build-overdrive/bin/Release). Confirm its help includes --rpc and --tensor-split.

After the new Vision processor update is installed, run in **Administrator PowerShell**:

~~~powershell
& "$env:ProgramData\VisionPC\Overdrive-Windows.ps1" -Action Configure -RuntimeDirectory "C:\path\to\RPC-build-output" -WorkerId "PASTE_THE_32_CHARACTER_MAC_ID" -MacBudgetGiB 8
~~~

This stages the extra runtime in its own directory, inventories binaries and DLLs with SHA-256, saves localhost ports and the worker identity in the administrator configuration, and preserves existing FUPCJ jobs and models. A manifest checks for later tampering but cannot certify that operator-supplied compiled binaries actually came from the pinned source; review the build.

The helper does not restart the live server, open a firewall port or enable an untested remote profile.

## 4. Benchmark and enable

With the Mac awake, its RPC helper running, both authenticated SSH forwards working and OBS/Streamlabs closed:

~~~powershell
& "$env:ProgramData\VisionPC\runtime\python.exe" "$env:ProgramData\VisionPC\setup_qwen_overdrive.py" --config "$env:ProgramData\VisionPC\config.json" --action tune --context 8192
~~~

The tuner measures layer counts 12/16/20/24/28 and two GPU splits (50:50 and 40:60), checking worker readiness before and after trials. It compares an end-to-end score based on loading, prompt and generation with the best existing PC Auto/CPU/Boost result. It only enables Overdrive when **at least 5% faster** by that score, after full conversation and near-context checks. The Mac memory target is advisory; monitor macOS Memory Pressure and Swap Used while testing. If it fails, the local receipt and original performance options remain untouched.

A successful result writes a separate **overdrive-ready.json** pinned to the original Ultra receipt, Mac identity, requested memory budget, specific RPC binary inventory and tested context. Restart the Vision processor only when its queue is idle, so subsequent Ultra prompts can choose the Mac automatically.

Check the configured connection:

~~~powershell
& "$env:ProgramData\VisionPC\Overdrive-Windows.ps1" -Action Status
~~~

Disable the option without deleting local models or receipts:

~~~powershell
& "$env:ProgramData\VisionPC\Overdrive-Windows.ps1" -Action Disable
~~~

Restart the processor after disabling when active jobs finish.

## Verification limitations

Run unit tests with:

~~~bash
python -m unittest discover -s vision-pc -p "test_local_*.py" -v
~~~

Offline unit tests validate the worker ID, strict localhost ports, stale receipt rejection, SHA-256 runtime inventory, GPU RPC flags, per-prompt routing and offline fallback. They do **not** prove real Metal + Vulkan RPC compatibility, a hard Mac 8GiB limit, or a throughput improvement.

Before production, test real WAN latency (including Tailscale relay versus direct), Mac sleep on battery, 8/9/10GiB allocations, SSH tunnel loss, OBS start protection, cancellation, long-context smoke and consecutive online/offline requests. Device placement is tensor/compute distribution, **not** pooled Windows+macOS addressable RAM. Per-token network synchronization may make an online Mac slower than FUPCJ alone; in that case the optional tuner refuses to enable it.
