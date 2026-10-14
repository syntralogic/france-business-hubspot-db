from fbdb.enrich.places import best_match, confidence, haversine_m

PLACE = {"id": "P1", "displayName": {"text": "Boulangerie Dupont"}, "location": {"latitude": 48.8690, "longitude": 2.3310}}


def test_haversine_known_distance():
    assert 110000 < haversine_m(48.0, 2.0, 49.0, 2.0) < 112000


def test_confidence_name_and_distance():
    assert confidence("DUPONT BOULANGERIE SARL", 48.8690, 2.3310, PLACE) >= 0.9
    far = confidence("DUPONT BOULANGERIE SARL", 48.9690, 2.3310, PLACE)       # ~11 km away: distance score 0
    assert far <= 0.6 and far < confidence("DUPONT BOULANGERIE SARL", 48.8690, 2.3310, PLACE)
    assert confidence("Garage Martin", 48.8690, 2.3310, PLACE) < 0.8          # same spot, other business
    assert confidence("X", 48.0, 2.0, {"displayName": {"text": "X"}}) == 0.6   # no location: name only


def test_best_match_picks_highest_and_handles_empty():
    other = {"id": "P2", "displayName": {"text": "Garage Martin"}, "location": PLACE["location"]}
    p, c = best_match("Boulangerie Dupont", 48.8690, 2.3310, [other, PLACE])
    assert p["id"] == "P1" and c > 0.9
    assert best_match("x", 0, 0, []) is None
