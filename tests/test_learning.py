from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from tassenerkennung.learning import LearningManager, LearningSession, safe_profile_id
from tassenerkennung.vessel_profiles import VesselProfiles


def manager():
    rng=np.random.default_rng(902)
    empty=np.clip(rng.normal(45,3,(56,48,3)),0,255).astype(np.uint8)
    cup=Image.fromarray(empty.copy())
    draw=ImageDraw.Draw(cup)
    draw.rounded_rectangle((13,9,36,50),radius=6,fill=(230,231,225))
    draw.ellipse((30,15,44,36),outline='white',width=3)
    profiles=VesselProfiles({'version':3,'camera':'camera.test','source_size':[1280,720],
        'zone_relative_box':[0,0,1,1],
        'samples':{'leer':[empty.tolist()]*3,'kaffeetasse':[np.asarray(cup).tolist()]*3}})
    coordinator=SimpleNamespace(engine=SimpleNamespace(profiles=profiles))
    return LearningManager(coordinator,None,SimpleNamespace()),profiles,empty,cup


def test_safe_user_profile_id():
    assert safe_profile_id('Espresso Gästetasse')=='benutzer_espresso_gastetasse'
    with pytest.raises(ValueError):safe_profile_id('!!!')


def test_candidate_rejects_duplicate_known_cup():
    learner,profiles,empty,cup=manager()
    duplicate=np.asarray(cup).tolist()
    learner.session=LearningSession('benutzer_kopie','Kopie',stage='review',samples=[duplicate]*9,empty_samples=[empty.tolist()]*3)
    with pytest.raises(ValueError,match='Kaffeetasse'):learner._validate_candidate()


def test_candidate_accepts_distinct_textured_cup():
    learner,_,empty,_=manager()
    rng=np.random.default_rng(829)
    samples=[rng.integers(30,230,(56,48,3),dtype=np.uint8).tolist() for _ in range(9)]
    learner.session=LearningSession('benutzer_gast','Gastbecher',stage='review',samples=samples,empty_samples=[empty.tolist()]*3)
    learner._validate_candidate()
