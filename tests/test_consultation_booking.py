"""Synthetic write protocol tests; no real school requests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from edupage_api import Edupage
from edupage_api.consultation_booking import ConsultationBooking
from edupage_api.exceptions import (
    ConsultationClosed,
    ConsultationError,
    ConsultationLimitReached,
    ConsultationNotOwned,
    ConsultationSlotTaken,
    ConsultationUncertain,
    ExpiredSessionException,
)
from edupage_api.registrations import (
    Consultation,
    ConsultationOverview,
    ConsultationReservation,
    ConsultationSlot,
)
from requests import Timeout


@pytest.fixture
def booking():
    api = Edupage()
    api.subdomain, api.is_logged_in = "school", True
    api.session = Mock()
    module = ConsultationBooking(api)
    future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
    block = Consultation(
        "b1",
        "t1",
        "Teacher",
        future,
        future + timedelta(hours=1),
        30,
        None,
        future - timedelta(days=1),
        teaches_student=True,
    )
    module.get_consultations = Mock(return_value=ConsultationOverview([block], []))
    slot = ConsultationSlot("b1", future, "free", False)
    module._detail = Mock(
        return_value=("/calendar/?cmd=KonzultacneHodinyVisit&jwid=dynamic", [slot])
    )
    module.get_consultation_slots = Mock(
        return_value=[ConsultationSlot("b1", future, "reserved", True, "v1")]
    )
    api.session.post.side_effect = [
        Mock(
            status_code=200,
            url="https://school.edupage.org/calendar/",
            text="",
            json=Mock(return_value={"status": "ok", "data": {"visitid": "v1"}}),
        ),
        Mock(
            status_code=200,
            url="https://school.edupage.org/calendar/",
            text="",
            json=Mock(return_value={"status": "ok"}),
        ),
    ]
    return module, future, block, slot


def test_dry_run_performs_no_write(booking):
    m, start, _, _ = booking
    plan = m.book_consultation_slot("b1", start, dry_run=True)
    assert plan["reserve"]["cas"] == str(int(start.timestamp()))
    m.edupage.session.post.assert_not_called()


def test_book_holds_confirms_and_verifies(booking):
    m, start, _, _ = booking
    result = m.book_consultation_slot("b1", start)
    assert result.visit_id == "v1"
    calls = m.edupage.session.post.call_args_list
    assert [c.args[0].split("akcia=")[1] for c in calls] == ["rezervuj", "confirm"]
    assert calls[1].kwargs["data"] == {"visitids": "v1"}
    m.get_consultation_slots.assert_called_once_with("b1")


@pytest.mark.parametrize("status", ["reserved", "temporary", "unknown"])
def test_not_free_rejected_before_write(booking, status):
    m, start, _, slot = booking
    slot.status = status
    with pytest.raises(ConsultationSlotTaken):
        m.book_consultation_slot("b1", start)
    m.edupage.session.post.assert_not_called()


def test_closed_window(booking):
    m, start, block, _ = booking
    block.booking_closes_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pytest.raises(ConsultationClosed):
        m.book_consultation_slot("b1", start)
    m.edupage.session.post.assert_not_called()


def test_existing_own_reservation_not_replaced(booking):
    m, start, _, slot = booking
    slot.own = True
    with pytest.raises(ConsultationLimitReached):
        m.book_consultation_slot("b1", start)


def test_timeout_no_retry_and_no_confirmation(booking):
    m, start, _, _ = booking
    m.edupage.session.post.side_effect = Timeout()
    with pytest.raises(ConsultationUncertain):
        m.book_consultation_slot("b1", start)
    assert m.edupage.session.post.call_count == 1


def test_confirmation_must_be_verified(booking):
    m, start, _, _ = booking
    m.get_consultation_slots.return_value = []
    with pytest.raises(ConsultationUncertain):
        m.book_consultation_slot("b1", start)
    assert m.edupage.session.post.call_count == 2


def test_expired_session(booking):
    m, start, _, _ = booking
    m.edupage.session.post.side_effect = None
    m.edupage.session.post.return_value = Mock(
        status_code=200, url="https://school.edupage.org/login/", text=""
    )
    with pytest.raises(ExpiredSessionException):
        m.book_consultation_slot("b1", start)


def test_cancel_requires_own_id(booking):
    m, _, _, _ = booking
    with pytest.raises(ConsultationNotOwned):
        m.cancel_consultation("foreign")
    m.edupage.session.post.assert_not_called()


def test_cancel_dry_run_and_actual_verified(booking):
    m, start, _, slot = booking
    m.get_consultations.return_value.reservations = [
        ConsultationReservation("b1", start, "v1")
    ]
    slot.own, slot.status, slot.visit_id = True, "reserved", "v1"
    plan = m.cancel_consultation("v1", dry_run=True)
    assert plan["cancel"] == {"konzultaciaid": "b1"}
    m.edupage.session.post.assert_not_called()
    m.get_consultation_slots.return_value = []
    m.cancel_consultation("v1")
    assert "akcia=zrus" in m.edupage.session.post.call_args.args[0]
    assert m.edupage.session.post.call_count == 1


def test_cancel_refuses_ambiguous_block(booking):
    m, start, _, slot = booking
    m.get_consultations.return_value.reservations = [
        ConsultationReservation("b1", start, "v1")
    ]
    slot.own, slot.visit_id = True, "v1"
    m._detail.return_value[1].append(
        ConsultationSlot("b1", start + timedelta(minutes=30), "reserved", True, "v2")
    )
    with pytest.raises(ConsultationNotOwned):
        m.cancel_consultation("v1")
    m.edupage.session.post.assert_not_called()


def test_aware_time_and_note_validation(booking):
    m, start, _, _ = booking
    with pytest.raises(ValueError):
        m.book_consultation_slot("b1", start.replace(tzinfo=None))
    with pytest.raises(ConsultationError) as error:
        m.book_consultation_slot("b1", start, note="Please call")
    assert error.value.code == "note_unsupported"


def test_extract_dynamic_form_does_not_execute_script(booking):
    m, _, _, _ = booking
    # Exercise real parser, including escaped URL delimiters.
    del m._detail
    m._request = Mock(
        return_value=Mock(
            text="""<script>var ignored="unsafe()";
        $j.post('\\/calendar\\/?jwid=hello\\u0026asDialog=1\\u0026cmd=KonzultacneHodinyVisit&akcia=rezervuj',{});
        </script>"""
        )
    )
    path, slots = m._detail("b1")
    assert "jwid=hello" in path
    assert slots == []


def test_nonexistent_time_is_not_reported_as_taken(booking):
    from edupage_api.exceptions import ConsultationSlotNotFound

    m, start, _, _ = booking
    with pytest.raises(ConsultationSlotNotFound) as error:
        m.book_consultation_slot("b1", start + timedelta(minutes=7), dry_run=True)
    assert error.value.code == "slot_not_found"
    m.edupage.session.post.assert_not_called()
