"""Xiaomi cloud request-crypto tests.

The network calls need an account, but the signing and encryption do not -- and
they are where a silent mistake would produce an empty device list rather than
an error, so they are pinned down here.
"""

import base64
import hashlib

import pytest

from hub.cloud import (
    CloudDevice,
    api_url,
    decrypt_rc4,
    encrypt_rc4,
    encrypted_params,
    generate_nonce,
    signature,
    signed_nonce,
)

SSECURITY = base64.b64encode(b"0123456789abcdef").decode()


# -- nonces ------------------------------------------------------------------


def test_nonce_is_twelve_bytes_of_base64():
    assert len(base64.b64decode(generate_nonce())) == 12


def test_nonce_encodes_the_time_in_minutes():
    millis = 1_700_000_000_000
    raw = base64.b64decode(generate_nonce(millis))
    assert int.from_bytes(raw[8:], "big") == millis // 60000


def test_nonces_differ_between_calls():
    assert generate_nonce(1_700_000_000_000) != generate_nonce(1_700_000_000_000)


def test_signed_nonce_is_sha256_of_the_decoded_pair():
    nonce = generate_nonce(1_700_000_000_000)
    expected = base64.b64encode(
        hashlib.sha256(base64.b64decode(SSECURITY) + base64.b64decode(nonce)).digest()
    ).decode()
    assert signed_nonce(SSECURITY, nonce) == expected


# -- rc4 ---------------------------------------------------------------------


@pytest.mark.parametrize("payload", ["", "x", '{"did":"123"}', "unicode: éè"])
def test_rc4_round_trip(payload):
    key = signed_nonce(SSECURITY, generate_nonce())
    assert decrypt_rc4(key, encrypt_rc4(key, payload)).decode() == payload


def test_rc4_discards_the_first_1024_bytes_of_keystream():
    """Without the discard, Xiaomi rejects every request -- pin the behaviour."""
    from Crypto.Cipher import ARC4

    key = signed_nonce(SSECURITY, generate_nonce())
    naive = ARC4.new(base64.b64decode(key)).encrypt(b"hello")
    assert base64.b64decode(encrypt_rc4(key, "hello")) != naive


def test_different_keys_give_different_ciphertext():
    a = signed_nonce(SSECURITY, generate_nonce())
    b = signed_nonce(SSECURITY, generate_nonce())
    assert encrypt_rc4(a, "hello") != encrypt_rc4(b, "hello")


# -- signatures --------------------------------------------------------------


def test_signature_covers_method_path_params_and_nonce():
    url = "https://de.api.io.mi.com/app/home/device_list"
    sig = signature(url, "POST", "NONCE", {"data": "{}"})
    expected = base64.b64encode(
        hashlib.sha1(b"POST&/home/device_list&data={}&NONCE").digest()
    ).decode()
    assert sig == expected


def test_signature_changes_with_any_input():
    url = "https://de.api.io.mi.com/app/home/device_list"
    base = signature(url, "POST", "N", {"data": "{}"})
    assert signature(url, "GET", "N", {"data": "{}"}) != base
    assert signature(url, "POST", "M", {"data": "{}"}) != base
    assert signature(url, "POST", "N", {"data": "{ }"}) != base


def test_encrypted_params_carry_everything_the_api_needs():
    url = "https://de.api.io.mi.com/app/home/device_list"
    nonce = generate_nonce()
    snonce = signed_nonce(SSECURITY, nonce)
    fields = encrypted_params(
        url, "POST", nonce, snonce, {"data": '{"a":1}'}, SSECURITY
    )
    assert set(fields) == {"data", "rc4_hash__", "signature", "ssecurity", "_nonce"}
    assert fields["_nonce"] == nonce
    assert fields["ssecurity"] == SSECURITY
    assert decrypt_rc4(snonce, fields["data"]).decode() == '{"a":1}'


def test_encrypted_params_do_not_mutate_the_caller_dict():
    url = "https://de.api.io.mi.com/app/home/device_list"
    nonce = generate_nonce()
    original = {"data": "{}"}
    encrypted_params(url, "POST", nonce, signed_nonce(SSECURITY, nonce), original, SSECURITY)
    assert original == {"data": "{}"}


# -- misc --------------------------------------------------------------------


@pytest.mark.parametrize(
    "server,expected",
    [
        ("cn", "https://api.io.mi.com/app"),
        ("de", "https://de.api.io.mi.com/app"),
        ("us", "https://us.api.io.mi.com/app"),
    ],
)
def test_api_url_per_region(server, expected):
    assert api_url(server) == expected


def test_device_parsing_from_the_api_shape():
    device = CloudDevice.from_api(
        {
            "did": "123456789",
            "name": "Vacuum",
            "model": "xiaomi.vacuum.b108gl",
            "token": "aa" * 16,
            "localip": "192.168.1.42",
            "mac": "AA:BB:CC:DD:EE:FF",
            "isOnline": True,
        },
        "de",
    )
    assert device.local_ip == "192.168.1.42"
    assert device.online is True
    assert "192.168.1.42" in device.describe()
    assert "xiaomi.vacuum.b108gl" in device.describe()


def test_missing_fields_do_not_break_parsing():
    device = CloudDevice.from_api({"did": "1"}, "de")
    assert device.local_ip is None and device.online is False
    assert "unknown" in device.describe()
