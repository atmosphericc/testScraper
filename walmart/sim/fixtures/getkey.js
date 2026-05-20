// Minimal canned PIE.js public-key blob. The real Walmart getkey.js sets
// `var PIE` as a JS object literal with the RSA public-key components.
// The Day 4 PIE module parses these values to encrypt the CVV.
//
// For the sim, we use a STATIC pubkey with known components. The Day 4
// test fixture will inject a real-test-keypair via SIM_STATE.pie_pubkey_response
// so the test can decrypt with the private half.
var PIE = {
  "K0c": "test-key-id-0",
  "key_id": "test-key-id-0",
  "phase": "1",
  "L": 3072,
  "n": "00",
  "e": "10001"
};
