cd "C:\Users\wwjoh\.gemini\antigravity\scratch\agent learn\0g-test"
$content = "0G storage test for AIP SafeHarness hackathon. " * 50
$content | Out-File -FilePath test.txt -Encoding utf8 -NoNewline
node upload.mjs
