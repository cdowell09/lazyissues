# Lazyissues

Lazyissues is a terminal workspace for viewing and updating GitHub issues across a set of repositories: personal work, a team, milestones, unassigned issues, and saved filters.

## Language

**Issue**:
A GitHub issue in one of the tracked repositories, identified as `repo#number`, with a title, status, and optional assignees, labels, body, milestone, and parent issue.
_Avoid_: Ticket.

**Repo set**:
The repositories Lazyissues tracks, shown together in every view.

**Status**:
An open issue's workflow position, a name such as Todo or In Progress. The user defines the statuses and their order, which is also the display order; Lazyissues has no built-in statuses. A status is the same whichever status source stores it. Names not in the user's list sort after it.
_Avoid_: State when referring to the workflow position; state is GitHub's open/closed.

**Status source**:
Where a repository stores its issues' statuses: either status labels or one project's Status field. Each repository in the repo set has exactly one.

**Status label**:
A label whose name matches a status, ignoring case and treating `-`, `_` and spaces alike, in a repository whose status source is labels. In a project-backed repository it is an ordinary label.
_Avoid_: Label when referring to a status label specifically.

**Active status**:
A status the user marks as meaning work has started. Moving an issue to an active status assigns the current user if it has no assignee.

**Project**:
A GitHub Project whose Status field is the status source for one or more repositories. An issue not on its repository's project has no status.

**Done**:
A closed issue, whatever its close reason, in every status source. A project's done options are reached by closing, not by a move.
_Avoid_: Closed as a status name; closing is not a status label.

**Close reason**:
How an issue was closed: completed, not planned, or duplicate.
_Avoid_: Resolution.

**Move**:
An attempt to change an issue's status: setting a new status in its status source, closing it with a close reason, or reopening it. An issue can only move to statuses its status source offers. Any status can move to any other; there are no workflow constraints.
_Avoid_: Transition.

**Milestone**:
A GitHub milestone in one repository, with progress measured by how many of its issues are done. Same-named milestones in different repositories are different milestones.
_Avoid_: Epic; Parent issue when referring to a milestone.

**Parent issue**:
An issue with sub-issues.

**Sub-issue**:
An issue that belongs to a parent issue. Views show it under its parent when the parent is in the same group.
_Avoid_: Child issue when referring to an issue in a milestone; that is a milestone's issue, not a sub-issue.

**Team roster**:
The GitHub users included in the Team view.

**My Work**:
The view of open issues assigned to the current GitHub user.

**Team**:
The view of work grouped by assignee, including the current user and other people in the team roster.

**Unassigned**:
Open issues with no assignee.

**Saved filter**:
A named GitHub issue search query whose matching issues can be viewed in the Filters tab.
_Avoid_: Search when referring to a saved query.

**Search**:
A text filter over loaded issues and, in the Team view, team members.

**Issue detail**:
The expanded view of an issue's fields, body, and comments.

**Activity**:
An issue's timeline of events and comments.

**Bulk action**:
A move or assignment applied to selected issues, with success, failure, or a reason for skipping each issue.
_Avoid_: Bulk upload when updating existing issues.

**Bulk upload**:
Creation of issues from CSV rows after preview and validation.
