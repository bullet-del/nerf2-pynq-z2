# Evidence gates

## Identity

- App service ID: `stage39-hardware-exact-qat-four-scene-app-v1`
- Board service ID: `stage39-hardware-exact-qat-four-scene-service-v1`
- FPGA build ID: `stage38-raw-only-universal-f95-v1`
- One bitstream is shared by s23, Meetingroom, Office1, and Office2. The Stage38 FPGA build ID is retained because Stage39 changes runtime weights, not RTL.

## Accuracy

The software gate covers all held-out test spectra: 1225 s23, 200 Meetingroom, 200 Office1, and 100 Office2. Each scene must have an original-PC-minus-integer mean SSIM gap no larger than 0.015. Training and test index sets must be disjoint.

The software gate predicts the exact integer datapath result. It becomes physical evidence only after a complete 32,400-ray self-test returns the same raw-Q15 SHA-256 from the board.

## Timing

The headline FPGA time is the physical full-map pipeline time for a 90×360 map, 32,400 rays, and 64 samples per ray. It excludes HTTP transfer and HDMI. Report HDMI and end-to-end wall time separately. Four scenes require 50 repeats each, median at most 1.2 s and P95 at most 1.5 s.

## Power

Use an inline meter at the board DC input. Record at least ten idle and ten inference samples for every scene. Report full-board active power, dynamic power as active minus idle, and joules per map from physical median time. Do not present Vivado estimates as measured power.

## Live judge proof

The judge nonce may be any 1–64 printable ASCII characters and may differ every run. The PC independently derives the challenged ray indices from the nonce and runtime identities. Show indices and values, not only PASS. The forced +1 LSB negative control must be detected as FAIL.
