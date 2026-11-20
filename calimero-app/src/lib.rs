//! Private Notetaker meetings: each meeting's transcript, notes and receipt, kept on the
//! app's attested nodes and readable only by the people who were in it.
//!
//! The notetaker (the desktop app in this repository) records and transcribes on the
//! user's own device and deletes the audio. What it sends here is text only: the
//! transcript, the notes and the receipt. No audio ever reaches this app.
//!
//! Who may do what:
//!
//! - **Add a meeting**: a member (or admin, or the owner) of the app's organization. The
//!   caller becomes the meeting's **host** and is always one of its participants.
//! - **Read a meeting, list meetings**: only that meeting's participants. Anyone else,
//!   including other members of the organization, gets `NotFound`, so a meeting's
//!   existence is not revealed. A guest of the organization (someone invited with a
//!   link, no membership) can read the meetings they were added to.
//! - **Add a participant**: only the host.
//! - **Leave a meeting**: any participant except the host removes their own access.
//! - **Delete a meeting**: only the host. It is gone for everyone.
//!
//! Storage: the meetings by id, and for each participant an index of the meetings they
//! can see, keyed `(startedAt, id)` so a list comes back in time order through one cursor.

use calimero_sdk::prelude::*;

/// The most meetings one `listMeetings` call returns.
const PAGE: usize = 1000;

/// The most participants one meeting may have.
const MAX_PARTICIPANTS: usize = 50;

/// One stretch of speech in the transcript. Times are seconds from the start.
#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize, AbiType)]
#[serde(rename_all = "camelCase")]
pub struct Segment {
    pub start: f64,
    pub end: f64,
    /// A speaker label, when the notetaker assigned one (`"Speaker 1"`); none otherwise.
    pub speaker: Option<String>,
    pub text: String,
}

/// A meeting as the notetaker sends it (its `meeting.json`, schema `notetaker.meeting/1`).
#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize, AbiType)]
#[serde(rename_all = "camelCase")]
pub struct MeetingInput {
    /// The notetaker's own id for the meeting; unique per app.
    pub id: String,
    pub title: String,
    /// ISO 8601 times, as the notetaker writes them.
    pub started_at: String,
    pub ended_at: String,
    pub length_seconds: f64,
    /// The spoken language (`"en"`).
    pub language: String,
    pub transcript: Vec<Segment>,
    /// The notes in Markdown; empty when the notetaker wrote none.
    pub notes_md: String,
    /// The meeting's receipt (what ran, where, when the audio was deleted) as JSON text.
    pub receipt_json: String,
}

/// A stored meeting: what the notetaker sent, its host and who may read it.
#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize, AbiType)]
#[serde(rename_all = "camelCase")]
pub struct Meeting {
    pub id: String,
    pub title: String,
    pub started_at: String,
    pub ended_at: String,
    pub length_seconds: f64,
    pub language: String,
    pub transcript: Vec<Segment>,
    pub notes_md: String,
    pub receipt_json: String,
    /// The account that added it.
    pub host: String,
    /// Everyone who may read it, the host first.
    pub participants: Vec<String>,
}

/// One line of `listMeetings`: enough to show a list without the transcript.
#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize, AbiType)]
#[serde(rename_all = "camelCase")]
pub struct MeetingSummary {
    pub id: String,
    pub title: String,
    pub started_at: String,
    pub length_seconds: f64,
    pub host: String,
    pub has_notes: bool,
}

#[app::state(version = 1)]
pub struct Meetings {
    /// Every meeting by id.
    meetings: UnorderedMap<String, Meeting>,
    /// For each participant's account, the meetings they can read, keyed `(startedAt, id)`.
    visible: UnorderedMap<String, UnorderedMap<(String, String), MeetingSummary>>,
}

/// A typed refusal: `422 APP_ERROR` with `{ code, fields }` on the wire.
#[app::error]
pub enum Error {
    /// The caller is not a signed-in account (or an API key acting for one).
    NotAnAccount,
    /// Only members of the organization may add meetings; a guest may only read.
    MembersOnly,
    /// A meeting with this id is already stored.
    AlreadyExists { id: String },
    /// No meeting with this id that the caller may see.
    NotFound { id: String },
    /// Only the meeting's host may do this.
    HostOnly { id: String },
    /// The host cannot leave their own meeting; they delete it instead.
    HostCannotLeave { id: String },
    /// An argument is empty or out of range.
    Invalid { field: String, reason: String },
}

/// A change, sent only to the meeting's participants.
#[app::event]
pub enum Event {
    /// A meeting was added, or the receiving account was added to one.
    MeetingShared { id: String, title: String, host: String },
    /// A participant left a meeting.
    ParticipantLeft { id: String, account: String },
    /// The host deleted a meeting.
    MeetingDeleted { id: String },
}

/// The account the caller acts for.
fn account() -> app::Result<String> {
    match env::caller() {
        env::Caller::Account(a) => Ok(a),
        env::Caller::ApiKey { account, .. } => Ok(account),
        _ => app::bail!(Error::NotAnAccount),
    }
}

/// The caller's account, if they are a member, admin or owner of the app's organization.
fn member() -> app::Result<String> {
    let me = account()?;
    let allowed = match env::org() {
        Some(_) => env::org_role(&me).is_some_and(|r| r >= env::OrgRole::Member),
        // No organization: only on the host target in tests; the owner and accounts the
        // test names count as members.
        None => true,
    };
    if !allowed {
        app::bail!(Error::MembersOnly);
    }
    Ok(me)
}

fn invalid(field: &str, reason: &str) -> Error {
    Error::Invalid {
        field: field.to_owned(),
        reason: reason.to_owned(),
    }
}

fn summary(m: &Meeting) -> MeetingSummary {
    MeetingSummary {
        id: m.id.clone(),
        title: m.title.clone(),
        started_at: m.started_at.clone(),
        length_seconds: m.length_seconds,
        host: m.host.clone(),
        has_notes: !m.notes_md.trim().is_empty(),
    }
}

/// The host first, then the others in the order given, without duplicates or blanks.
fn participant_list(host: &str, others: Vec<String>) -> app::Result<Vec<String>> {
    let mut list = vec![host.to_owned()];
    for p in others {
        let p = p.trim().to_owned();
        if p.is_empty() {
            app::bail!(invalid("participants", "an account is empty"));
        }
        if !list.contains(&p) {
            list.push(p);
        }
    }
    if list.len() > MAX_PARTICIPANTS {
        app::bail!(invalid("participants", "more than 50"));
    }
    Ok(list)
}

#[app::logic]
impl Meetings {
    /// No meetings yet.
    #[app::init]
    pub fn init() -> app::Result<Self> {
        Ok(Self::load())
    }

    /// Stores a meeting the caller hosted, readable by the caller and `participants`
    /// (accounts of the organization: members or guests). Emits `MeetingShared` to each.
    pub fn add_meeting(
        &mut self,
        meeting: MeetingInput,
        participants: Vec<String>,
    ) -> app::Result<()> {
        let host = member()?;
        if meeting.id.trim().is_empty() {
            app::bail!(invalid("meeting.id", "empty"));
        }
        if !meeting.length_seconds.is_finite() || meeting.length_seconds < 0.0 {
            app::bail!(invalid("meeting.lengthSeconds", "not a length"));
        }
        if self.meetings.contains(&meeting.id)? {
            app::bail!(Error::AlreadyExists { id: meeting.id });
        }
        let participants = participant_list(&host, participants)?;
        let stored = Meeting {
            id: meeting.id,
            title: meeting.title,
            started_at: meeting.started_at,
            ended_at: meeting.ended_at,
            length_seconds: meeting.length_seconds,
            language: meeting.language,
            transcript: meeting.transcript,
            notes_md: meeting.notes_md,
            receipt_json: meeting.receipt_json,
            host: host.clone(),
            participants: participants.clone(),
        };
        let line = summary(&stored);
        let _previous = self.meetings.insert(&stored.id, &stored)?;
        for p in &participants {
            let _ = self
                .visible
                .get(p)?
                .insert(&(stored.started_at.clone(), stored.id.clone()), &line)?;
        }
        env::emit(
            Event::MeetingShared {
                id: stored.id,
                title: stored.title,
                host,
            },
            Audience::Accounts(participants),
        )?;
        Ok(())
    }

    /// The whole meeting, for its participants only; `NotFound` for anyone else.
    #[app::view]
    pub fn get_meeting(&self, id: String) -> app::Result<Meeting> {
        let me = account()?;
        match self.meetings.get(&id)? {
            Some(m) if m.participants.contains(&me) => Ok(m),
            _ => Err(Error::NotFound { id }.into()),
        }
    }

    /// The meetings the caller can read, oldest first (at most 1,000).
    #[app::view]
    pub fn list_meetings(&self) -> app::Result<Vec<MeetingSummary>> {
        let me = account()?;
        let mut out = Vec::new();
        for entry in self.visible.get(&me)?.iter() {
            let (_, line) = entry?;
            out.push(line);
            if out.len() == PAGE {
                break;
            }
        }
        Ok(out)
    }

    /// Lets one more account read a meeting. Host only. Adding someone already in it does
    /// nothing.
    pub fn add_participant(&mut self, id: String, account: String) -> app::Result<()> {
        let me = self::account()?;
        let Some(mut m) = self.meetings.get(&id)?.filter(|m| m.participants.contains(&me))
        else {
            app::bail!(Error::NotFound { id });
        };
        if m.host != me {
            app::bail!(Error::HostOnly { id });
        }
        let account = account.trim().to_owned();
        if account.is_empty() {
            app::bail!(invalid("account", "empty"));
        }
        if m.participants.contains(&account) {
            return Ok(());
        }
        if m.participants.len() >= MAX_PARTICIPANTS {
            app::bail!(invalid("participants", "more than 50"));
        }
        m.participants.push(account.clone());
        let line = summary(&m);
        let _ = self.meetings.insert(&id, &m)?;
        let _ = self
            .visible
            .get(&account)?
            .insert(&(m.started_at.clone(), id.clone()), &line)?;
        env::emit(
            Event::MeetingShared {
                id,
                title: m.title,
                host: m.host,
            },
            Audience::Accounts(vec![account]),
        )?;
        Ok(())
    }

    /// Removes the caller's own access to a meeting. The host deletes instead.
    pub fn leave_meeting(&mut self, id: String) -> app::Result<()> {
        let me = account()?;
        let Some(mut m) = self.meetings.get(&id)?.filter(|m| m.participants.contains(&me))
        else {
            app::bail!(Error::NotFound { id });
        };
        if m.host == me {
            app::bail!(Error::HostCannotLeave { id });
        }
        m.participants.retain(|p| p != &me);
        let _ = self.meetings.insert(&id, &m)?;
        let _ = self.visible.get(&me)?.remove(&(m.started_at.clone(), id.clone()))?;
        let mut told = m.participants.clone();
        told.push(me.clone());
        env::emit(
            Event::ParticipantLeft { id, account: me },
            Audience::Accounts(told),
        )?;
        Ok(())
    }

    /// Deletes a meeting for everyone. Host only.
    pub fn delete_meeting(&mut self, id: String) -> app::Result<()> {
        let me = account()?;
        let Some(m) = self.meetings.get(&id)?.filter(|m| m.participants.contains(&me)) else {
            app::bail!(Error::NotFound { id });
        };
        if m.host != me {
            app::bail!(Error::HostOnly { id });
        }
        let key = (m.started_at.clone(), id.clone());
        for p in &m.participants {
            let _ = self.visible.get(p)?.remove(&key)?;
        }
        let _ = self.meetings.remove(&id)?;
        env::emit(
            Event::MeetingDeleted { id },
            Audience::Accounts(m.participants),
        )?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use calimero_sdk::env::{mock, Caller, OrgRole};

    fn as_(who: &str) {
        mock::with_mut(|s| s.caller = Caller::Account(who.to_owned()));
    }

    /// An app in org "team": ann and bob members, gus a guest, eve a member too.
    fn app() -> Meetings {
        mock::reset();
        mock::with_mut(|s| {
            s.org = Some("team".into());
            for (a, r) in [
                ("owner", OrgRole::Owner),
                ("ann", OrgRole::Member),
                ("bob", OrgRole::Member),
                ("eve", OrgRole::Member),
                ("gus", OrgRole::Guest),
            ] {
                s.roles.insert(a.into(), r);
            }
        });
        Meetings::load()
    }

    fn input(id: &str, started: &str) -> MeetingInput {
        MeetingInput {
            id: id.into(),
            title: format!("Meeting {id}"),
            started_at: started.into(),
            ended_at: started.into(),
            length_seconds: 120.5,
            language: "en".into(),
            transcript: vec![Segment {
                start: 0.0,
                end: 2.5,
                speaker: None,
                text: "Hello.".into(),
            }],
            notes_md: "## Key points\n- One".into(),
            receipt_json: r#"{"audio_deleted":true}"#.into(),
        }
    }

    fn code(e: calimero_sdk::error::AppError) -> String {
        format!("{e:?}")
    }

    fn ids(app: &Meetings) -> Vec<String> {
        app.list_meetings().unwrap().into_iter().map(|s| s.id).collect()
    }

    /// Replays `scenarios/meetings.json` on the mock host, calling the methods directly, so
    /// the scenario's expectations are checked even where the component cannot be built.
    /// Steps the platform answers (`refused`) and malformed-argument steps (`BadArgs`, the
    /// generated dispatcher's) are left to the real harness in `tests/scenarios.rs`.
    #[test]
    fn the_scenario_file_holds_on_the_mock_host() {
        use serde_json::{json, Value};
        let text = include_str!("../scenarios/meetings.json");
        let steps: Vec<Value> = serde_json::from_str(text).unwrap();
        let mut a = app();
        let acct = |v: &Value| -> Value {
            // "@ann" is the account "ann" in the harness.
            let s = serde_json::to_string(v).unwrap().replace("\"@", "\"");
            serde_json::from_str(&s).unwrap()
        };
        for (n, step) in steps.iter().enumerate() {
            let expect = &step["expect"];
            if expect.get("refused").is_some() || expect["error"] == "BadArgs" {
                continue;
            }
            as_(step["as"].as_str().unwrap());
            let args = acct(&step["args"]);
            let s = |k: &str| args[k].as_str().unwrap().to_owned();
            let got: Result<Value, calimero_sdk::error::AppError> =
                match step["method"].as_str().unwrap() {
                    "addMeeting" => a
                        .add_meeting(
                            serde_json::from_value(args["meeting"].clone()).unwrap(),
                            serde_json::from_value(args["participants"].clone()).unwrap(),
                        )
                        .map(|()| Value::Null),
                    "getMeeting" => a.get_meeting(s("id")).map(|m| json!(m)),
                    "listMeetings" => a.list_meetings().map(|l| json!(l)),
                    "addParticipant" => a.add_participant(s("id"), s("account")).map(|()| Value::Null),
                    "leaveMeeting" => a.leave_meeting(s("id")).map(|()| Value::Null),
                    "deleteMeeting" => a.delete_meeting(s("id")).map(|()| Value::Null),
                    other => panic!("step {n}: unknown method {other}"),
                };
            match (got, expect.get("ok"), expect.get("error")) {
                (Ok(v), Some(want), None) => assert_eq!(v, acct(want), "step {n}"),
                (Err(e), None, Some(code)) => {
                    assert_eq!(&json!(e.code), code, "step {n}");
                    if let Some(f) = expect.get("fields") {
                        assert_eq!(&e.fields, f, "step {n}");
                    }
                }
                (got, _, _) => panic!("step {n}: got {got:?}, expected {expect}"),
            }
        }
    }

    #[test]
    fn host_and_participants_read_it_others_do_not() {
        let mut a = app();
        as_("ann");
        a.add_meeting(input("m1", "2026-11-20T10:00:00Z"), vec!["gus".into(), "ann".into()])
            .unwrap();
        let m = a.get_meeting("m1".into()).unwrap();
        assert_eq!(m.host, "ann");
        assert_eq!(m.participants, vec!["ann", "gus"]);
        assert_eq!(m.transcript.len(), 1);

        as_("gus");
        assert_eq!(a.get_meeting("m1".into()).unwrap().notes_md, "## Key points\n- One");
        assert_eq!(ids(&a), vec!["m1"]);

        // A member of the org who was not in the meeting sees nothing, not even that it exists.
        as_("bob");
        assert!(code(a.get_meeting("m1".into()).unwrap_err()).contains("NotFound"));
        assert!(ids(&a).is_empty());
        // Nor does the org's owner.
        as_("owner");
        assert!(code(a.get_meeting("m1".into()).unwrap_err()).contains("NotFound"));
    }

    #[test]
    fn events_go_only_to_participants() {
        let mut a = app();
        as_("ann");
        a.add_meeting(input("m1", "t1"), vec!["bob".into()]).unwrap();
        let (name, _, aud) = mock::with_mut(|s| s.events.last().cloned()).unwrap();
        assert_eq!(name, "MeetingShared");
        assert_eq!(aud, Audience::Accounts(vec!["ann".into(), "bob".into()]));
    }

    #[test]
    fn guests_cannot_add_and_ids_are_unique() {
        let mut a = app();
        as_("gus");
        assert!(code(a.add_meeting(input("m1", "t"), vec![]).unwrap_err()).contains("MembersOnly"));
        as_("ann");
        a.add_meeting(input("m1", "t"), vec![]).unwrap();
        as_("bob");
        assert!(code(a.add_meeting(input("m1", "t"), vec![]).unwrap_err()).contains("AlreadyExists"));
        mock::with_mut(|s| s.caller = Caller::System);
        assert!(code(a.add_meeting(input("m2", "t"), vec![]).unwrap_err()).contains("NotAnAccount"));
    }

    #[test]
    fn bad_input_is_refused() {
        let mut a = app();
        as_("ann");
        assert!(code(a.add_meeting(input(" ", "t"), vec![]).unwrap_err()).contains("Invalid"));
        let mut neg = input("m1", "t");
        neg.length_seconds = -1.0;
        assert!(code(a.add_meeting(neg, vec![]).unwrap_err()).contains("Invalid"));
        assert!(code(a.add_meeting(input("m1", "t"), vec!["".into()]).unwrap_err()).contains("Invalid"));
        let many = (0..60).map(|n| format!("p{n}")).collect();
        assert!(code(a.add_meeting(input("m1", "t"), many).unwrap_err()).contains("Invalid"));
        // Nothing was stored by the refused calls.
        assert!(ids(&a).is_empty());
    }

    #[test]
    fn list_is_in_time_order() {
        let mut a = app();
        as_("ann");
        a.add_meeting(input("late", "2026-11-20T15:00:00Z"), vec![]).unwrap();
        a.add_meeting(input("early", "2026-11-20T09:00:00Z"), vec![]).unwrap();
        assert_eq!(ids(&a), vec!["early", "late"]);
        let s = &a.list_meetings().unwrap()[0];
        assert!(s.has_notes);
        assert_eq!(s.host, "ann");
    }

    #[test]
    fn only_the_host_adds_participants() {
        let mut a = app();
        as_("ann");
        a.add_meeting(input("m1", "t"), vec!["bob".into()]).unwrap();
        as_("bob");
        assert!(code(a.add_participant("m1".into(), "eve".into()).unwrap_err()).contains("HostOnly"));
        as_("eve");
        assert!(code(a.add_participant("m1".into(), "eve".into()).unwrap_err()).contains("NotFound"));
        as_("ann");
        a.add_participant("m1".into(), "eve".into()).unwrap();
        a.add_participant("m1".into(), "eve".into()).unwrap(); // already in: no change
        assert_eq!(a.get_meeting("m1".into()).unwrap().participants, vec!["ann", "bob", "eve"]);
        as_("eve");
        assert_eq!(ids(&a), vec!["m1"]);
    }

    #[test]
    fn a_participant_leaves_and_the_host_cannot() {
        let mut a = app();
        as_("ann");
        a.add_meeting(input("m1", "t"), vec!["bob".into()]).unwrap();
        assert!(code(a.leave_meeting("m1".into()).unwrap_err()).contains("HostCannotLeave"));
        as_("bob");
        a.leave_meeting("m1".into()).unwrap();
        assert!(ids(&a).is_empty());
        assert!(code(a.get_meeting("m1".into()).unwrap_err()).contains("NotFound"));
        as_("ann");
        assert_eq!(a.get_meeting("m1".into()).unwrap().participants, vec!["ann"]);
    }

    #[test]
    fn only_the_host_deletes_and_it_is_gone_for_everyone() {
        let mut a = app();
        as_("ann");
        a.add_meeting(input("m1", "t"), vec!["bob".into()]).unwrap();
        a.add_meeting(input("m2", "u"), vec!["bob".into()]).unwrap();
        as_("bob");
        assert!(code(a.delete_meeting("m1".into()).unwrap_err()).contains("HostOnly"));
        as_("eve");
        assert!(code(a.delete_meeting("m1".into()).unwrap_err()).contains("NotFound"));
        as_("ann");
        a.delete_meeting("m1".into()).unwrap();
        assert_eq!(ids(&a), vec!["m2"]);
        as_("bob");
        assert_eq!(ids(&a), vec!["m2"]);
        assert!(code(a.get_meeting("m1".into()).unwrap_err()).contains("NotFound"));
        // The id is free again.
        as_("ann");
        a.add_meeting(input("m1", "t"), vec![]).unwrap();
    }
}
