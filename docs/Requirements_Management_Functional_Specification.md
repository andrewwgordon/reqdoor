# Functional Specification: Enterprise Requirements Management Tool

## 1. Purpose
This document defines the functional specification for an enterprise Requirements Management (RM) platform inspired by the domain model and capabilities of IBM DOORS Next Generation (DOORS NG). The platform supports the lifecycle of requirements, specifications, models, test artefacts, risks, change requests, baselines, reviews, traceability, configuration management, and governance.

---

# 2. Vision

Provide a collaborative, configuration-aware requirements management environment enabling stakeholders to:

- Define and manage requirements.
- Maintain complete end-to-end traceability.
- Control change through versioning and baselines.
- Collaborate through reviews and discussions.
- Analyse impact across interconnected artefacts.
- Demonstrate compliance through auditability.
- Support systems engineering, software engineering and regulated industries.

---

# 3. Domain Model

## 3.1 Core Entities

### Project
A logical container for requirements management activities.

Attributes:
- Project ID
- Name
- Description
- Owner
- Lifecycle State
- Creation Date
- Tags
- Access Policies

Relationships:
- Contains Artefact Types
- Contains Artefacts
- Contains Modules
- Contains Folders
- Contains Baselines
- Contains Reviews
- Contains Users and Roles

---

### Artefact Type
Defines structure and behaviour of artefacts.

Examples:
- Stakeholder Requirement
- System Requirement
- Software Requirement
- Use Case
- Business Rule
- Risk
- Test Case
- Design Element
- Feature
- Epic
- User Story
- Defect

Attributes:
- Type ID
- Name
- Display Name
- Description
- Schema Definition
- Mandatory Fields
- Workflow Definition

---

### Artefact
Represents a managed engineering object.

Attributes:
- Artefact ID
- Global Identifier
- Title
- Rich Text Content
- Status
- Priority
- Owner
- Created Date
- Modified Date
- Category
- Tags

Relationships:
- Belongs to Project
- Has Artefact Type
- Has Versions
- Participates in Relationships
- Appears in Modules
- Appears in Reviews

---

### Artefact Version
Immutable snapshot of an artefact at a point in time.

Attributes:
- Version ID
- Version Number
- Creation Date
- Author
- Change Summary
- Electronic Signature

Capabilities:
- Version comparison
- Historical restoration
- Traceability preservation

---

### Artefact Relationship Type
Defines permissible trace links.

Examples:
- Satisfies
- Refines
- Elaborates
- Depends On
- Implements
- Verifies
- Validates
- Mitigates
- References
- Derives From
- Conflicts With

Attributes:
- Relationship Type ID
- Name
- Directionality
- Rules

---

### Artefact Relationship
Persistent traceability link.

Attributes:
- Relationship ID
- Source Artefact
- Target Artefact
- Relationship Type
- Created By
- Created Date

---

### Baseline
Configuration-controlled snapshot.

Attributes:
- Baseline ID
- Name
- Description
- Creation Date
- Owner
- Baseline Version

Contains:
- Collection of Artefact Versions
- Relationship Versions

Capabilities:
- Regulatory evidence
- Historical comparison
- Release management

---

### Collection / Module
Ordered document-style representation of artefacts.

Capabilities:
- Hierarchical requirements
- Section management
- Numbering
- Change tracking

---

## 3.2 Extended Domain Model

### Review
Formal review workflow.

Attributes:
- Review ID
- Title
- Status
- Due Date

Relationships:
- Targets Artefacts
- Targets Baselines
- Has Reviewers
- Contains Comments

### Comment
Collaborative discussion object.

### Approval
Electronic approval record.

### Change Set
Logical grouping of modifications.

### Variant
Product-line engineering configuration.

### Requirement Classification
- Functional
- Non-functional
- Safety
- Security
- Performance
- Regulatory

### Media Artefact
Represents image, video, audio or document evidence.

Attributes:
- Media Type
- File Format
- Metadata
- AI Extracted Content

Capabilities:
- Image annotation
- Video timeline annotation
- OCR extraction
- Requirement candidate extraction

### Diagram Artefact
- UML
- SysML
- BPMN
- Architecture Views

### Test Artefact
- Test Case
- Test Procedure
- Test Result

### Risk Artefact
- Hazard
- Threat
- Failure Mode

---

# 4. Functional Requirements

## FR-1 Project Management

The system shall:
- Create projects.
- Archive projects.
- Clone projects.
- Import/export projects.
- Define project templates.
- Manage project permissions.

## FR-2 Artefact Management

The system shall:
- Create artefacts.
- Edit artefacts.
- Delete artefacts.
- Bulk modify artefacts.
- Define custom attributes.
- Support rich text editing.
- Support attachments.

## FR-3 Type Administration

The system shall:
- Create artefact types.
- Configure schemas.
- Define validation rules.
- Configure workflows.

## FR-4 Version Management

The system shall:
- Create immutable versions.
- Compare versions.
- Restore prior versions.
- Show change history.

## FR-5 Traceability

The system shall:
- Create relationships.
- Validate relationship rules.
- Visualise trace graphs.
- Provide suspect link analysis.
- Identify orphan requirements.

## FR-6 Baseline Management

The system shall:
- Create baselines.
- Compare baselines.
- Restore baselines.
- Audit baseline contents.

## FR-7 Review Management

The system shall:
- Create reviews.
- Assign reviewers.
- Capture comments.
- Capture approvals.
- Generate review reports.

## FR-8 Change Management

The system shall:
- Create change requests.
- Assess impact.
- Manage change sets.
- Support approval workflows.

## FR-9 Search and Analysis

The system shall:
- Support full-text search.
- Support semantic search.
- Save queries.
- Provide analytics dashboards.

## FR-10 Reporting

The system shall:
- Generate compliance reports.
- Generate trace matrices.
- Export PDF, Word and Excel.

## FR-11 Media Analysis

The system shall:
- Upload images and videos.
- Perform OCR.
- Extract requirement candidates.
- Detect objects and annotations.
- Link extracted information to artefacts.

## FR-12 AI Assistance

The system shall:
- Suggest requirements.
- Detect duplicates.
- Recommend trace links.
- Generate summaries.
- Identify inconsistencies.

---

# 5. Non-Functional Requirements

## Performance
- Search response under 2 seconds for typical queries.
- Support projects exceeding 1 million artefacts.

## Availability
- 99.9% availability target.

## Security
- SSO integration.
- Multi-factor authentication.
- Encryption in transit and at rest.
- Audit logging.

## Scalability
- Horizontal scaling.
- Multi-project operation.

## Compliance
- ISO 26262
- DO-178C
- IEC 61508
- ISO 13485
- ASPICE

---

# 6. Use Cases

## UC-01 Create Requirement
Actor: Requirements Engineer

Flow:
1. Create artefact.
2. Select artefact type.
3. Enter attributes.
4. Save artefact.
5. Version created.

## UC-02 Create Trace Link
Actor: Systems Engineer

Flow:
1. Open artefact.
2. Select create relationship.
3. Select target.
4. Select relationship type.
5. Save.

## UC-03 Build Baseline
Actor: Configuration Manager

Flow:
1. Select project state.
2. Create baseline.
3. Capture versions.
4. Release baseline.

## UC-04 Perform Impact Analysis
Actor: Change Manager

Flow:
1. Select changed artefact.
2. View trace network.
3. View impacted artefacts.
4. Produce impact report.

## UC-05 Conduct Review
Actor: Reviewer

Flow:
1. Receive review request.
2. Examine artefacts.
3. Add comments.
4. Approve or reject.

## UC-06 Extract Requirements From Video
Actor: Business Analyst

Flow:
1. Upload video.
2. System analyses frames.
3. Generate candidate requirements.
4. User validates results.
5. Requirements created.

## UC-07 Verify Coverage
Actor: Test Manager

Flow:
1. Select release baseline.
2. Generate trace matrix.
3. Verify requirement-test coverage.

---

# 7. User Stories

## Project Administration

- As a project administrator, I want to create project templates so that new projects follow organisational standards.
- As a project administrator, I want role-based permissions so that access is controlled.

## Requirements Engineering

- As a requirements engineer, I want to create requirements so that stakeholder needs are captured.
- As a requirements engineer, I want version history so that I can understand changes.
- As a requirements engineer, I want requirement workflows so that governance is maintained.

## Systems Engineering

- As a systems engineer, I want traceability links so that requirements can be decomposed and validated.
- As a systems engineer, I want relationship visualisation so that dependencies are understood.

## Change Management

- As a change manager, I want impact analysis so that change risks are understood.
- As a change manager, I want suspect link detection so that traceability remains accurate.

## Reviews

- As a reviewer, I want threaded comments so that issues are collaboratively resolved.
- As a reviewer, I want approval workflows so that releases can be authorised.

## Compliance

- As an auditor, I want immutable baselines so that evidence is preserved.
- As an auditor, I want audit trails so that all changes are traceable.

## AI Capabilities

- As a business analyst, I want AI-generated requirement candidates so that discovery is accelerated.
- As a product owner, I want duplicate detection so that redundancy is reduced.
- As a quality engineer, I want coverage gap analysis so that verification completeness improves.

---

# 8. Workflow Model

Requirement Lifecycle:

Draft -> Proposed -> In Review -> Approved -> Implemented -> Verified -> Released -> Obsolete

Change Workflow:

Submitted -> Analysed -> Approved -> Implemented -> Verified -> Closed

Review Workflow:

Planned -> Active -> Comment Resolution -> Approved -> Closed

---

# 9. Reporting and Analytics

The platform shall provide:

- Traceability matrices
- Coverage reports
- Baseline comparison reports
- Change velocity metrics
- Requirement volatility metrics
- Review completion metrics
- Compliance dashboards

---

# 10. Future Extensions

- Digital thread integration
- PLM integration
- Model-based systems engineering support
- Knowledge graphs
- Generative AI co-authoring
- Requirement quality scoring
- Automated regulatory mapping
