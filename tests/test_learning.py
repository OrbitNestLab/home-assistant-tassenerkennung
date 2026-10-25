from types import SimpleNamespace

import numpy as np
import pytest

from tassenerkennung.learning import LearningManager, LearningSession, safe_profile_id
from fixtures import profiles


def manager():
    known=profiles()
    coordinator=SimpleNamespace(engine=SimpleNamespace(profiles=known))
    return LearningManager(coordinator,None,SimpleNamespace()),known


def test_safe_user_profile_id():
    assert safe_profile_id('Espresso Gästetasse')=='benutzer_espresso_gastetasse'
    with pytest.raises(ValueError):safe_profile_id('!!!')


def test_candidate_rejects_duplicate_known_cup():
    learner,profiles=manager()
    duplicate=np.clip(profiles.samples['kaffeetasse'][0]*255,0,255).astype(np.uint8).tolist()
    learner.session=LearningSession('benutzer_kopie','Kopie',stage='review',samples=[duplicate]*9)
    with pytest.raises(ValueError,match='Kaffeetasse'):learner._validate_candidate()


def test_candidate_accepts_distinct_textured_cup():
    learner,_=manager()
    rng=np.random.default_rng(829)
    samples=[rng.integers(30,230,(28,26,3),dtype=np.uint8).tolist() for _ in range(9)]
    learner.session=LearningSession('benutzer_gast','Gastbecher',stage='review',samples=samples)
    learner._validate_candidate()
