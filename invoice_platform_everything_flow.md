---
subtitle: End-to-End Operational Flowchart
title: Invoice Reconciliation Platform --- Everything Flow
version: 1.0
---

# Invoice Reconciliation Platform --- Everything Flow

> **Purpose:** This document is the operational flow map of the entire
> Invoice platform.
>
> It is intentionally written as **flowcharts**, not as a component
> architecture document.
>
> Every important workflow shows:
>
> -   process steps
> -   decision points
> -   YES / NO branches
> -   failure paths
> -   retry paths
> -   recovery paths
> -   human-review paths
> -   financial-update boundaries
> -   final success / unresolved states

The flow follows the current product boundary: authentication and
company workspace, customer management, invoice management, CSV
bank-statement/payment import, deterministic reconciliation, human
review, dashboard, audit, and settings. Bank APIs, email/ERP
integrations, forecasting, analytics, AI copilot, multi-currency, and
mobile remain future scope.

The product principle is:

**Deterministic financial logic → evidence → controlled decision → human
authority → atomic financial update → audit.**

------------------------------------------------------------------------

# 1. Master --- Complete Platform Flow

``` mermaid
flowchart LR
    U([User]) --> L[Login / Register]

    L --> AUTH{Authentication successful?}
    AUTH -->|NO| AE[Show authentication error]
    AE --> RETRYAUTH[Retry login / reset password]
    RETRYAUTH --> L
    AUTH -->|YES| WS[Open Company Workspace]

    WS --> CUS[Customer Management]
    WS --> INV[Invoice Management]
    WS --> BANK[Bank Statement / Payment Import]
    WS --> REV[Review Center]
    WS --> DASH[Dashboard]
    WS --> AUD[Audit Logs]
    WS --> SET[Settings]

    INV --> IFLOW[Invoice Processing Flow]
    BANK --> PFLOW[Payment Processing Flow]

    IFLOW --> READY[Invoices available for reconciliation]
    PFLOW --> RECON[Reconciliation Engine]

    READY --> RECON

    RECON --> DEC{Reconciliation decision}

    DEC -->|MATCHED / SAFE SUGGESTION| SUG[Create reconciliation suggestion]
    DEC -->|AMBIGUOUS| REVIEW[Review Center]
    DEC -->|UNMATCHED| REVIEW
    DEC -->|REVIEW_REQUIRED| REVIEW

    SUG --> REVIEW
    REVIEW --> HUMAN{Accountant decision}

    HUMAN -->|APPROVE| FIN[Financial Update Boundary]
    HUMAN -->|REJECT| REJ[Reject suggestion]
    HUMAN -->|MANUAL ALLOCATION| MAN[Create manual allocation]
    HUMAN -->|CLOSE / DEFER| DEF[Keep item unresolved / deferred]

    MAN --> FIN
    REJ --> AUD
    DEF --> AUD

    FIN --> TX{Atomic financial transaction successful?}

    TX -->|YES| UPDATE[Update allocation + invoice balance + invoice status]
    UPDATE --> AUDIT[Write audit event]
    AUDIT --> DASH
    DASH --> DONE([Reconciliation Complete])

    TX -->|NO| ROLLBACK[Rollback financial transaction]
    ROLLBACK --> FFAIL{Retryable failure?}
    FFAIL -->|YES| FRET[Bounded retry with safe idempotency]
    FRET --> FIN
    FFAIL -->|NO / EXHAUSTED| FAILED[Failed — Reviewable Recovery State]
    FAILED --> REV

    CUS --> AUD
    INV --> AUD
    BANK --> AUD
    FIN --> AUD
    AUD --> AUDIT

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff,stroke-width:1.5px;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111,stroke-width:1.5px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff,stroke-width:1.5px;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff,stroke-width:2px;

    class U actor;
    class L,WS,CUS,INV,BANK,REV,DASH,AUD,SET,IFLOW,PFLOW,RECON,SUG,FIN,UPDATE,AUDIT,AE,RETRYAUTH,REJ,MAN,DEF,FRET,ROLLBACK process;
    class AUTH,DEC,HUMAN,TX,FFAIL decision;
    class IFLOW,PFLOW,RECON,FIN work;
    class DONE success;
    class FAILED,AE failure;
    class REVIEW review;
```

------------------------------------------------------------------------

# 2. Authentication + Company Workspace

``` mermaid
flowchart LR
    START([User]) --> REG[Register]
    REG --> VALID{Registration data valid?}

    VALID -->|NO| ERR[Show validation error]
    ERR --> REG
    VALID -->|YES| CREATE[Create Company + Owner User]

    CREATE --> TX{Creation transaction successful?}
    TX -->|NO| CERR[Show registration failure]
    CERR --> RETRY[Retry registration]
    RETRY --> REG

    TX -->|YES| VERIFY[Email Verification]

    VERIFY --> VERIFIED{Email verified?}
    VERIFIED -->|NO| RESEND[Resend verification]
    RESEND --> VERIFY
    VERIFIED -->|YES| READY[Workspace Ready]

    READY --> LOGIN[Login]
    LOGIN --> AUTH{Credentials valid?}

    AUTH -->|NO| LOGINERR[Show login error]
    LOGINERR --> LOCK{Rate limit reached?}
    LOCK -->|YES| WAIT[Temporarily block further attempts]
    LOCK -->|NO| LOGIN
    WAIT --> LOGIN

    AUTH -->|YES| SESSION[Create authenticated session]
    SESSION --> OPEN[Open isolated company workspace]

    OPEN --> ROLE{Authorized for requested action?}
    ROLE -->|NO| DENY[Permission denied]
    ROLE -->|YES| ACTION[Continue]

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;

    class START actor;
    class REG,ERR,CREATE,CERR,RETRY,VERIFY,RESEND,LOGIN,LOGINERR,WAIT,SESSION,OPEN,DENY,ACTION process;
    class VALID,TX,VERIFIED,AUTH,LOCK,ROLE decision;
    class READY,OPEN success;
```

------------------------------------------------------------------------

# 3. Customer Management Flow

Customer is an identity layer. It stores customer information, aliases,
known bank/payment identifiers, and history needed by reconciliation. It
does not itself make financial reconciliation decisions.

``` mermaid
flowchart LR
    START([Accountant]) --> OPEN[Open Customers]
    OPEN --> ACTION{Choose action}

    ACTION -->|CREATE| CREATE[Enter customer information]
    ACTION -->|EDIT| EDIT[Open existing customer]
    ACTION -->|ARCHIVE| ARCH[Archive customer]
    ACTION -->|DELETE| DELETE[Request deletion]

    CREATE --> VALID{Data valid?}
    VALID -->|NO| ERR[Show validation error]
    ERR --> CREATE
    VALID -->|YES| SAVE[Save customer]

    EDIT --> EVALID{Updated data valid?}
    EVALID -->|NO| EERR[Show validation error]
    EERR --> EDIT
    EVALID -->|YES| ESAVE[Save changes]

    ARCH --> LINKED{Active linked records prevent archive?}
    LINKED -->|YES| AERR[Show reason / keep active]
    LINKED -->|NO| ASAVE[Archive customer]

    DELETE --> DEP{Linked invoices / payments exist?}
    DEP -->|YES| DERR[Block deletion + explain dependency]
    DEP -->|NO| DCONFIRM{User confirms deletion?}
    DCONFIRM -->|NO| OPEN
    DCONFIRM -->|YES| DSAVE[Delete customer]

    SAVE --> AUD[Audit action]
    ESAVE --> AUD
    ASAVE --> AUD
    DSAVE --> AUD

    AUD --> DONE([Customer state updated])

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;

    class START actor;
    class OPEN,CREATE,EDIT,ARCH,DELETE,ERR,SAVE,EERR,ESAVE,AERR,ASAVE,DERR,DSAVE,AUD process;
    class ACTION,VALID,EVALID,LINKED,DEP,DCONFIRM decision;
    class DONE success;
```

------------------------------------------------------------------------

# 4. Invoice Upload + Processing Flow

Supported invoice sources in the current product scope include PDF
upload, CSV import, and manual entry. PDF invoices can pass through OCR.

``` mermaid
flowchart LR
    START([Accountant]) --> UPLOAD[Upload Invoice]
    UPLOAD --> FILE{File / input valid?}

    FILE -->|NO| REJECT[Reject input + show reason]
    REJECT --> RETRY{Can user correct and retry?}
    RETRY -->|YES| UPLOAD
    RETRY -->|NO| ENDFAIL([Invoice not imported])

    FILE -->|YES| STORE[Store original invoice file / input]
    STORE --> SOURCE{OCR required?}

    SOURCE -->|NO| DATA[Use supplied structured data]
    SOURCE -->|YES| OCR[Send to OCR]

    OCR --> OCROK{OCR successful?}
    OCROK -->|NO| ORTRY{Retryable OCR failure?}
    ORTRY -->|YES| ORETRY[Bounded OCR retry]
    ORETRY --> OCR
    ORTRY -->|NO / EXHAUSTED| OREVIEW[Mark processing failure / Needs Review]
    OREVIEW --> REVIEW([Human review])

    OCROK -->|YES| DATA
    DATA --> VALID[Validate extracted / supplied invoice data]
    VALID --> VOK{Invoice valid?}

    VOK -->|NO| IREVIEW[Needs Review]
    IREVIEW --> REVIEW

    VOK -->|YES| DUP{Duplicate invoice?}
    DUP -->|YES| DUPLICATE[Flag duplicate / prevent unsafe duplicate creation]
    DUPLICATE --> AUD1[Audit event]
    AUD1 --> REVIEW

    DUP -->|NO| SAVE[Save invoice]
    SAVE --> AUD[Audit event]
    AUD --> READY([Invoice available for reconciliation])

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff,stroke-width:2px;

    class START actor;
    class UPLOAD,REJECT,RETRY,STORE,DATA,OCR,ORTRY,ORETRY,OREVIEW,VALID,IREVIEW,DUPLICATE,AUD1,SAVE,AUD process;
    class FILE,SOURCE,OCROK,VOK,DUP,RETRY decision;
    class READY success;
    class ENDFAIL failure;
    class REVIEW,OREVIEW,IREVIEW,DUPLICATE review;
```

------------------------------------------------------------------------

# 5. Bank Statement → Payment Import Flow

MVP payment input is CSV bank-statement upload. Future bank APIs/payment
gateways can feed the same downstream payment flow.

``` mermaid
flowchart LR
    START([Accountant]) --> UPLOAD[Upload Bank Statement CSV]
    UPLOAD --> FILE{File valid?}

    FILE -->|NO| FERR[Reject file + show reason]
    FERR --> RETRY[Correct file and retry]
    RETRY --> UPLOAD

    FILE -->|YES| PARSE[Parse transactions]
    PARSE --> PARSEOK{Parsing successful?}

    PARSEOK -->|NO| PERR[Parsing failed]
    PERR --> PRETRY{Retryable?}
    PRETRY -->|YES| PARSERETRY[Retry parsing]
    PARSERETRY --> PARSE
    PRETRY -->|NO| PFAIL([Import failed — reviewable])

    PARSEOK -->|YES| ROWS{All rows valid?}
    ROWS -->|NO| PARTIAL[Create partial import / flag invalid rows]
    ROWS -->|YES| IMPORT[Create payment records]

    PARTIAL --> IMPORT
    IMPORT --> DUP{Duplicate transaction?}

    DUP -->|YES| FLAG[Flag / skip duplicate safely]
    DUP -->|NO| PAYMENT[Payment marked Unreconciled]

    FLAG --> NEXT{More valid rows?}
    PAYMENT --> NEXT
    NEXT -->|YES| IMPORT
    NEXT -->|NO| AUD[Audit import]

    AUD --> QUEUE[Queue reconciliation]
    QUEUE --> READY([Payments available for reconciliation])

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class START actor;
    class UPLOAD,FERR,RETRY,PARSE,PERR,PRETRY,PARSERETRY,PARTIAL,IMPORT,FLAG,PAYMENT,AUD,QUEUE process;
    class FILE,PARSEOK,ROWS,DUP,NEXT decision;
    class READY success;
    class PFAIL failure;
```

------------------------------------------------------------------------

# 6. Reconciliation Engine --- Complete Flow

This is the core product flow. Financial matching, allocation,
confidence, and decisioning are deterministic. AI may assist with
language understanding or explanation, but it does not make the
financial decision.

``` mermaid
flowchart LR
    P([Unreconciled Payment]) --> INTAKE[Payment Intake]
    INTAKE --> VALID{Payment usable?}

    VALID -->|NO| PERR[Mark payment invalid / review]
    VALID -->|YES| IDENT[Customer Identification]

    IDENT --> EVIDID[Check known bank accounts]
    EVIDID --> ALIAS[Check customer aliases]
    ALIAS --> NARR[Evaluate payment narration / reference]
    NARR --> HIST[Check historical confirmed mappings]
    HIST --> CUSTOMERS[Generate candidate customers]

    CUSTOMERS --> CUSTOMER{Payer uniquely identifiable?}
    CUSTOMER -->|NO| PAYERREVIEW[Payer Unresolved / Ambiguous]
    CUSTOMER -->|YES| CAND[Candidate Invoice Finder]

    CAND --> FILTER[Find eligible outstanding invoices]
    FILTER --> BOUND{Candidate set truncated?}
    BOUND -->|YES| FLAGTRUNC[Record truncated candidate set]
    BOUND -->|NO| MATCH
    FLAGTRUNC --> MATCH[Matching Engine]

    MATCH --> ONE[Check one-to-one amount match]
    ONE --> PARTIAL[Check partial-payment possibilities]
    PARTIAL --> COMBO[Check multi-invoice combinations]
    COMBO --> ALLOC{Valid allocation candidates found?}

    ALLOC -->|NO| UNMATCHED[UNMATCHED]
    ALLOC -->|YES| UNIQUE{One clearly supported candidate?}

    UNIQUE -->|NO| AMBIG[AMBIGUOUS]
    UNIQUE -->|YES| EVID[Build structured evidence]

    EVID --> SCORE[Calculate deterministic heuristic score]
    SCORE --> DECISION[Decision Engine]

    DECISION --> D{Decision}
    D -->|MATCHED / SAFE SUGGESTION| SUGGEST[Create suggestion]
    D -->|REVIEW_REQUIRED| REVIEW[Review Center]
    D -->|AMBIGUOUS| REVIEW
    D -->|UNMATCHED| REVIEW

    SUGGEST --> REVIEW
    PAYERREVIEW --> REVIEW
    UNMATCHED --> REVIEW
    AMBIG --> REVIEW

    REVIEW --> HUMAN{Human decision}
    HUMAN -->|Approve| ALLOCATE[Proceed to financial update]
    HUMAN -->|Reject| REJECT[Reject suggestion]
    HUMAN -->|Change allocation| MANUAL[Create manual allocation]
    HUMAN -->|Defer| DEFER[Remain unresolved]

    ALLOCATE --> FIN[Financial Update]
    MANUAL --> FIN

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff,stroke-width:2px;

    class P actor;
    class INTAKE,IDENT,EVIDID,ALIAS,NARR,HIST,CUSTOMERS,CAND,FILTER,FLAGTRUNC,MATCH,ONE,PARTIAL,COMBO,EVID,SCORE,DECISION,SUGGEST,REVIEW,PAYERREVIEW,UNMATCHED,AMBIG,REJECT,MANUAL,DEFER,ALLOCATE,FIN process;
    class VALID,CUSTOMER,BOUND,ALLOC,UNIQUE,D,HUMAN decision;
    class FIN,MATCH,EVID,SCORE work;
    class PERR,UNMATCHED failure;
    class REVIEW,PAYERREVIEW,AMBIG review;
```

------------------------------------------------------------------------

# 7. Customer Identification --- Evidence Branches

The engine does not guess when available evidence cannot uniquely
distinguish payers.

``` mermaid
flowchart LR
    P([Payment]) --> BANK[Known bank account / payment identifier]
    BANK --> B{Unique customer?}

    B -->|YES| CUSTOMER[Customer identified]
    B -->|NO| ALIAS[Check aliases]

    ALIAS --> A{Unique customer?}
    A -->|YES| CUSTOMER
    A -->|NO| REF[Check reference / UTR / narration]

    REF --> R{Strong differentiating evidence?}
    R -->|YES| CUSTOMER
    R -->|NO| HIST[Check confirmed historical mappings]

    HIST --> H{Unique supported customer?}
    H -->|YES| CUSTOMER
    H -->|NO| AMB[PAYER_UNRESOLVED]

    CUSTOMER --> NEXT[Continue to candidate invoices]
    AMB --> REVIEW[Review Center]

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff,stroke-width:2px;

    class P actor;
    class BANK,ALIAS,REF,HIST,CUSTOMER,NEXT process;
    class B,A,R,H decision;
    class CUSTOMER,NEXT success;
    class AMB,REVIEW review;
```

------------------------------------------------------------------------

# 8. Candidate Invoice Generation

Candidate bounds are a performance boundary, not proof that excluded
invoices do not exist. If the candidate set is truncated, the result
must carry that fact forward.

``` mermaid
flowchart LR
    START([Identified Customer]) --> FETCH[Load eligible invoices]
    FETCH --> FILTER1[Filter by customer]
    FILTER1 --> FILTER2[Filter by outstanding balance]
    FILTER2 --> FILTER3[Filter by compatible currency / transaction context]
    FILTER3 --> FILTER4[Exclude cancelled / ineligible invoices]

    FILTER4 --> LIMIT{Candidate limit reached?}

    LIMIT -->|NO| COMPLETE[Candidate set complete]
    LIMIT -->|YES| TRUNC[Candidate set truncated]
    TRUNC --> FLAG[Record truncation metadata]

    COMPLETE --> MATCH[Matching Engine]
    FLAG --> MATCH

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;

    class START actor;
    class FETCH,FILTER1,FILTER2,FILTER3,FILTER4,TRUNC,FLAG,MATCH process;
    class LIMIT decision;
    class COMPLETE,MATCH success;
```

------------------------------------------------------------------------

# 9. Matching Engine --- One-to-One, Partial, Combination

``` mermaid
flowchart LR
    P([Payment Amount]) --> EXACT[Check exact one-invoice match]
    EXACT --> E{Exact candidate exists?}

    E -->|YES| PARTIAL[Check whether payment is partial against invoice]
    E -->|NO| PARTIAL

    PARTIAL --> MULTI[Generate valid multi-invoice combinations]
    MULTI --> SUM{Combination total equals payment?}

    SUM -->|NO| NONE[No valid allocation]
    SUM -->|YES| MULTI2{More than one valid allocation?}

    MULTI2 -->|YES| AMBIG[Multiple valid allocations]
    MULTI2 -->|NO| UNIQUE[Unique valid allocation]

    UNIQUE --> EVID[Evidence collection]
    AMBIG --> REVIEW[Review Required]
    NONE --> REVIEW

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class P actor;
    class EXACT,PARTIAL,MULTI,MULTI2,UNIQUE,EVID,REVIEW process;
    class E,SUM decision;
    class UNIQUE,EVID success;
    class NONE failure;
    class AMBIG,REVIEW review;
```

------------------------------------------------------------------------

# 10. Evidence → Score → Decision

A heuristic score is not a calibrated probability. It is evidence used
by the decision policy.

``` mermaid
flowchart LR
    MATCH([Candidate Allocation]) --> E1[Customer identity evidence]
    E1 --> E2[Amount evidence]
    E2 --> E3[Reference / UTR evidence]
    E3 --> E4[Due-date / invoice-age evidence]
    E4 --> E5[Outstanding-balance evidence]
    E5 --> E6[Historical confirmed-payment evidence]

    E6 --> BUILDER[Build structured evidence object]
    BUILDER --> SCORE[Calculate deterministic heuristic score]

    SCORE --> LABEL[Label score as deterministic heuristic]
    LABEL --> POLICY{Decision policy satisfied?}

    POLICY -->|YES — sufficiently supported| SUGGEST[MATCHED / SUGGESTION]
    POLICY -->|NO — conflicting or insufficient| REVIEW[REVIEW_REQUIRED]

    SUGGEST --> EXPLAIN[Generate explainable evidence summary]
    REVIEW --> EXPLAIN

    EXPLAIN --> HUMAN([Accountant sees evidence])

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff,stroke-width:2px;

    class MATCH actor;
    class E1,E2,E3,E4,E5,E6,BUILDER,SCORE,LABEL,EXPLAIN process;
    class POLICY decision;
    class SCORE,BUILDER work;
    class SUGGEST success;
    class REVIEW review;
    class HUMAN actor;
```

------------------------------------------------------------------------

# 11. Review Center --- Every Human Outcome

``` mermaid
flowchart LR
    ITEM([Review Item]) --> SHOW[Show payment + suggested allocation + evidence + conflicts]
    SHOW --> STALE{Underlying data changed?}

    STALE -->|YES| REFRESH[Refresh / invalidate stale suggestion]
    REFRESH --> REEVAL[Re-run reconciliation]
    REEVAL --> SHOW

    STALE -->|NO| DECIDE{Accountant decision}

    DECIDE -->|APPROVE| APPROVE[Approve proposed allocation]
    DECIDE -->|REJECT| REJECT[Reject suggestion]
    DECIDE -->|CHANGE| CHANGE[Select different invoice / allocation]
    DECIDE -->|DEFER| DEFER[Keep unresolved]

    APPROVE --> FIN[Financial Update]
    CHANGE --> VALIDATE{Manual allocation valid?}

    VALIDATE -->|NO| ERR[Show allocation error]
    ERR --> CHANGE
    VALIDATE -->|YES| FIN

    REJECT --> AUD[Audit rejection]
    DEFER --> AUD
    AUD --> DONE([Review action recorded])

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;

    class ITEM actor;
    class SHOW,REFRESH,REEVAL,APPROVE,REJECT,CHANGE,DEFER,ERR,FIN,AUD,DONE process;
    class STALE,DECIDE,VALIDATE decision;
    class FIN work;
    class DONE success;
```

------------------------------------------------------------------------

# 12. Financial Update --- The Critical Transaction Boundary

The reconciliation engine proposes. The financial update layer is the
only place where the approved financial state is mutated.

``` mermaid
flowchart LR
    START([Approved Allocation]) --> LOAD[Load payment + invoices]
    LOAD --> AUTH{User still authorized?}

    AUTH -->|NO| DENY[Reject operation]
    AUTH -->|YES| STATE{State still current?}

    STATE -->|NO| STALE[Stale / concurrent change detected]
    STALE --> REVIEW[Return to Review Center]
    STATE -->|YES| IDEMP{Already applied?}

    IDEMP -->|YES| DONE([Idempotent success / no duplicate mutation])
    IDEMP -->|NO| TX[Begin database transaction]

    TX --> ALLOC[Create financial allocation]
    ALLOC --> BAL[Update invoice outstanding balance]
    BAL --> STATUS[Update invoice status]
    STATUS --> AUD[Create audit event]

    AUD --> COMMIT{Commit successful?}

    COMMIT -->|YES| DONE
    COMMIT -->|NO| ROLLBACK[Rollback transaction]

    ROLLBACK --> RETRYABLE{Retryable infrastructure failure?}
    RETRYABLE -->|YES| RETRY[Bounded safe retry]
    RETRY --> TX
    RETRYABLE -->|NO / EXHAUSTED| FAIL[Failed — reviewable recovery state]
    FAIL --> REVIEW

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class START actor;
    class LOAD,DENY,STALE,REVIEW,TX,ALLOC,BAL,STATUS,AUD,ROLLBACK,RETRY,FAIL process;
    class AUTH,STATE,IDEMP,COMMIT,RETRYABLE decision;
    class TX,ALLOC,BAL,STATUS work;
    class DONE success;
    class FAIL,DENY failure;
    class REVIEW,STALE review;
```

------------------------------------------------------------------------

# 13. Concurrency --- Two Accountants Cannot Corrupt the Same Payment

``` mermaid
flowchart LR
    P([Review Item]) --> A1[Accountant A opens item]
    P --> A2[Accountant B opens same item]

    A1 --> APPROVE1[Accountant A approves]
    A2 --> APPROVE2[Accountant B approves]

    APPROVE1 --> CHECK1[Validate current state / version]
    APPROVE2 --> CHECK2[Validate current state / version]

    CHECK1 --> V1{Still current?}
    CHECK2 --> V2{Still current?}

    V1 -->|YES| UPDATE1[Apply financial update]
    V1 -->|NO| STALE1[Reject stale action]

    V2 -->|YES| UPDATE2[Apply financial update]
    V2 -->|NO| STALE2[Reject stale action]

    UPDATE1 --> COMMIT1[Commit]
    UPDATE2 --> COMMIT2[Commit]

    STALE1 --> INFORM1[Tell user item was already changed]
    STALE2 --> INFORM2[Tell user item was already changed]

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;

    class P actor;
    class A1,A2,APPROVE1,APPROVE2,CHECK1,CHECK2,UPDATE1,UPDATE2,COMMIT1,COMMIT2,STALE1,STALE2,INFORM1,INFORM2 process;
    class V1,V2 decision;
    class COMMIT1,COMMIT2 success;
    class STALE1,STALE2,INFORM1,INFORM2 failure;
```

------------------------------------------------------------------------

# 14. Idempotency --- Safe Retries Without Duplicate Money

``` mermaid
flowchart LR
    REQUEST([Operation Request]) --> KEY[Build / receive idempotency key]
    KEY --> EXIST{Operation already completed?}

    EXIST -->|YES| RETURN[Return existing result]
    EXIST -->|NO| PROCESS[Process operation]

    PROCESS --> SUCCESS{Operation successful?}
    SUCCESS -->|YES| STORE[Persist result against idempotency key]
    STORE --> RETURN

    SUCCESS -->|NO| FAIL{Retryable?}
    FAIL -->|YES| RETRY[Retry safely]
    RETRY --> PROCESS
    FAIL -->|NO| ERROR[Persist failure / expose recovery state]

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;

    class REQUEST actor;
    class KEY,RETURN,PROCESS,STORE,RETRY,ERROR process;
    class EXIST,SUCCESS,FAIL decision;
    class RETURN,STORE success;
    class ERROR failure;
```

------------------------------------------------------------------------

# 15. External / AI Dependency Failure Isolation

The core financial workflow must remain safe when optional external
processing is unavailable.

``` mermaid
flowchart LR
    EVENT([Platform Event]) --> DEP{Dependency required?}

    DEP -->|OCR| OCR[OCR Provider]
    DEP -->|LLM explanation / narration| LLM[LLM Provider]
    DEP -->|Email / notification| EMAIL[Email / Notification Service]
    DEP -->|Core financial DB| DB[Database]

    OCR --> O{OCR available?}
    O -->|YES| EXTRACT[Continue invoice processing]
    O -->|NO| OPEND[Mark OCR Pending / Retryable]

    LLM --> L{LLM available?}
    L -->|YES| EXPLAIN[Generate explanation]
    L -->|NO| LFALL[Continue deterministic workflow without explanation]

    EMAIL --> E{Email available?}
    E -->|YES| SENT[Send notification]
    E -->|NO| EPEND[Notification Pending / Retry]

    DB --> D{Database available?}
    D -->|YES| COMMIT[Continue transaction]
    D -->|NO| DBFAIL[Fail safely — no partial financial mutation]

    OPEND --> REVIEW[Visible recovery state]
    LFALL --> CORE[Core reconciliation continues]
    EPEND --> RECOVER[Recoverable notification state]
    DBFAIL --> RECOVERY[Recovery / alert / retry path]

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class EVENT actor;
    class OCR,LLM,EMAIL,DB,EXTRACT,OPEND,EXPLAIN,LFALL,SENT,EPEND,COMMIT,DBFAIL,REVIEW,CORE,RECOVER,RECOVERY process;
    class DEP,O,L,E,D decision;
    class EXTRACT,EXPLAIN,SENT,COMMIT,CORE success;
    class DBFAIL failure;
    class OPEND,EPEND,REVIEW,RECOVERY review;
```

------------------------------------------------------------------------

# 16. Retry / Recovery Policy

Retry is not "do it forever." Retry only operations that are safe to
retry, with bounded attempts and idempotency.

``` mermaid
flowchart LR
    FAIL([Operation Failed]) --> TYPE{Failure type?}

    TYPE -->|Transient / retryable| SAFE{Operation safe to retry?}
    TYPE -->|Validation / business rule| PERM[Permanent business failure]
    TYPE -->|Unknown / unexpected| UNKNOWN[Unexpected failure]

    SAFE -->|NO| MANUAL[Stop + manual recovery]
    SAFE -->|YES| COUNT{Attempts remaining?}

    COUNT -->|YES| BACKOFF[Exponential backoff + jitter]
    BACKOFF --> RETRY[Retry operation]
    RETRY --> RESULT{Success?}

    RESULT -->|YES| DONE([Recovered])
    RESULT -->|NO| COUNT

    COUNT -->|NO| EXHAUST[Retries exhausted]
    EXHAUST --> MANUAL

    PERM --> REVIEW[Reviewable failure state]
    UNKNOWN --> ALERT[Log + alert + preserve state]
    ALERT --> REVIEW

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class FAIL actor;
    class SAFE,PERM,UNKNOWN,MANUAL,BACKOFF,RETRY,EXHAUST,ALERT,REVIEW process;
    class TYPE,SAFE,COUNT,RESULT decision;
    class DONE success;
    class PERM,UNKNOWN,EXHAUST failure;
    class MANUAL,REVIEW review;
```

------------------------------------------------------------------------

# 17. Audit Flow

Audit is attached to important business and financial actions.

``` mermaid
flowchart LR
    EVENT([Important Action]) --> CAPTURE[Capture actor + tenant + object + timestamp]
    CAPTURE --> STATE[Capture relevant previous / resulting state]
    STATE --> REASON[Capture reason / decision context]
    REASON --> CORR[Attach request / correlation identifier]
    CORR --> WRITE[Write audit event]

    WRITE --> OK{Audit write successful?}
    OK -->|YES| DONE([Action has audit trail])
    OK -->|NO| FAIL[Do not silently lose critical audit evidence]

    FAIL --> POLICY{Can parent operation safely continue?}
    POLICY -->|NO| ROLLBACK[Fail / rollback critical operation]
    POLICY -->|YES| RECOVER[Queue / recover according to audit policy]

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class EVENT actor;
    class CAPTURE,STATE,REASON,CORR,WRITE,FAIL,ROLLBACK,RECOVER process;
    class OK,POLICY decision;
    class DONE success;
    class FAIL,ROLLBACK failure;
    class RECOVER review;
```

------------------------------------------------------------------------

# 18. Dashboard Flow

The dashboard is a read-oriented view of current system state. It does
not become a second financial engine.

``` mermaid
flowchart LR
    USER([User]) --> DASH[Open Dashboard]
    DASH --> LOAD[Load current metrics]

    LOAD --> DATA{Data available?}
    DATA -->|YES| SHOW[Show current state]
    DATA -->|NO| ERR[Show dashboard error]
    ERR --> RETRY[Retry]
    RETRY --> LOAD

    SHOW --> METRICS[Pending invoices / Paid today / Unmatched / Review / Outstanding]
    METRICS --> NAV{User wants action?}

    NAV -->|YES| MODULE[Open relevant module]
    NAV -->|NO| DONE([Dashboard complete])

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;

    class USER actor;
    class DASH,LOAD,ERR,RETRY,SHOW,METRICS,MODULE process;
    class DATA,NAV decision;
    class DONE success;
    class ERR failure;
```

------------------------------------------------------------------------

# 19. Settings Flow

``` mermaid
flowchart LR
    USER([Authorized User]) --> SETTINGS[Open Settings]
    SETTINGS --> AREA{Settings area}

    AREA -->|Company| COMPANY[Company settings]
    AREA -->|Users| USERS[User management]
    AREA -->|Threshold / policy| THRESH[Reconciliation policy settings]
    AREA -->|Currency / timezone| LOCAL[Locale settings]

    COMPANY --> AUTH1{Authorized?}
    USERS --> AUTH2{Authorized?}
    THRESH --> AUTH3{Authorized?}
    LOCAL --> AUTH4{Authorized?}

    AUTH1 -->|NO| DENY1[Permission denied]
    AUTH2 -->|NO| DENY2[Permission denied]
    AUTH3 -->|NO| DENY3[Permission denied]
    AUTH4 -->|NO| DENY4[Permission denied]

    AUTH1 -->|YES| SAVE1[Validate + save]
    AUTH2 -->|YES| SAVE2[Validate + save]
    AUTH3 -->|YES| SAVE3[Validate + save]
    AUTH4 -->|YES| SAVE4[Validate + save]

    SAVE1 --> AUD[Audit change]
    SAVE2 --> AUD
    SAVE3 --> AUD
    SAVE4 --> AUD

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;

    class USER actor;
    class SETTINGS,COMPANY,USERS,THRESH,LOCAL,DENY1,DENY2,DENY3,DENY4,SAVE1,SAVE2,SAVE3,SAVE4,AUD process;
    class AREA,AUTH1,AUTH2,AUTH3,AUTH4 decision;
```

------------------------------------------------------------------------

# 20. Invoice Lifecycle

``` mermaid
flowchart LR
    START([Invoice Input]) --> RECEIVED[Received]
    RECEIVED --> PROCESSING[Processing]

    PROCESSING --> EXTRACTED{Extraction / data valid?}
    EXTRACTED -->|YES| PENDING[Pending]
    EXTRACTED -->|NO| RETRY{Retryable processing failure?}

    RETRY -->|YES| WAIT[Retry Wait]
    WAIT --> PROCESSING

    RETRY -->|NO / EXHAUSTED| REVIEW[Needs Review]

    PENDING --> PAYMENT{Payment allocated?}
    PAYMENT -->|NO| PENDING
    PAYMENT -->|PARTIAL| PARTIAL[Partial]
    PAYMENT -->|FULL| PAID[Paid]

    PARTIAL --> PAYMENT
    PENDING --> CANCEL{Cancelled?}
    PARTIAL --> CANCEL
    CANCEL -->|YES| CANCELLED[Cancelled]

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff,stroke-width:2px;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class START actor;
    class RECEIVED,PROCESSING,WAIT,PENDING,PARTIAL,PAID,CANCELLED process;
    class EXTRACTED,RETRY,PAYMENT,CANCEL decision;
    class PAID success;
    class REVIEW failure;
```

------------------------------------------------------------------------

# 21. Payment Lifecycle

``` mermaid
flowchart LR
    START([Bank Transaction]) --> IMPORTED[Imported]
    IMPORTED --> NORMALIZE[Normalize]
    NORMALIZE --> DUP{Duplicate?}

    DUP -->|YES| DUPLICATE[Duplicate / ignored or flagged]
    DUP -->|NO| UNREC[Unreconciled]

    UNREC --> RECON[Run reconciliation]
    RECON --> RESULT{Result}

    RESULT -->|Matched suggestion| MATCHED[Matched / awaiting controlled update]
    RESULT -->|Review required| REVIEW[Review]
    RESULT -->|Unmatched| UNMATCHED[Unmatched]

    MATCHED --> APPROVE{Financial update approved?}
    APPROVE -->|YES| ALLOCATED[Allocated]
    APPROVE -->|NO| REVIEW

    REVIEW --> DECIDE{User resolves?}
    DECIDE -->|YES| ALLOCATED
    DECIDE -->|NO| REVIEW

    UNMATCHED --> REVIEW

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class START actor;
    class IMPORTED,NORMALIZE,DUPLICATE,UNREC,RECON,MATCHED,REVIEW,UNMATCHED,ALLOCATED process;
    class DUP,RESULT,APPROVE,DECIDE decision;
    class ALLOCATED success;
    class DUPLICATE,UNMATCHED failure;
    class REVIEW review;
```

------------------------------------------------------------------------

# 22. AI Boundary --- What AI Can and Cannot Do

``` mermaid
flowchart LR
    INPUT([Financial / textual input]) --> TYPE{Task type}

    TYPE -->|OCR assistance| OCR[AI / OCR extraction assistance]
    TYPE -->|Narration understanding| NARR[Interpret natural language]
    TYPE -->|Explanation| EXPLAIN[Explain deterministic result]
    TYPE -->|Reminder drafting| REMIND[Draft reminder]
    TYPE -->|Financial decision| BLOCK[DO NOT delegate to AI]
    TYPE -->|Payment allocation| BLOCK
    TYPE -->|Confidence calculation| BLOCK
    TYPE -->|Invoice update| BLOCK
    TYPE -->|Financial logic| BLOCK

    OCR --> VALID[Deterministic validation]
    NARR --> EVIDENCE[Convert into structured evidence]
    EXPLAIN --> USER([Accountant])
    REMIND --> USER

    BLOCK --> ALG[Deterministic algorithm / policy]
    ALG --> DECIDE[Controlled decision]
    DECIDE --> USER

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;

    class INPUT actor;
    class OCR,NARR,EXPLAIN,REMIND,VALID,EVIDENCE,USER,ALG,DECIDE process;
    class TYPE decision;
    class DECIDE,USER success;
    class BLOCK failure;
```

------------------------------------------------------------------------

# 23. Reconciliation Run Failure / Recovery

``` mermaid
flowchart LR
    START([Reconciliation Run]) --> QUEUED[Queued]
    QUEUED --> RUNNING[Running]

    RUNNING --> RESULT{Run completed?}
    RESULT -->|YES| COMPLETE[Completed]
    RESULT -->|NO| FAIL[Failed]

    FAIL --> RETRYABLE{Retryable?}
    RETRYABLE -->|YES| WAIT[Retry Pending]
    WAIT --> RETRY[Retry Run]
    RETRY --> RUNNING

    RETRYABLE -->|NO / EXHAUSTED| REVIEW[Failed — Reviewable]
    REVIEW --> RESOLVE{Can state be safely recovered?}

    RESOLVE -->|YES| RECOVER[Recover / rerun from safe boundary]
    RECOVER --> RUNNING
    RESOLVE -->|NO| MANUAL[Manual investigation]

    COMPLETE --> AUD[Audit run result]

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class START actor;
    class QUEUED,RUNNING,COMPLETE,FAIL,WAIT,RETRY,REVIEW,RECOVER,MANUAL,AUD process;
    class RESULT,RETRYABLE,RESOLVE decision;
    class COMPLETE,AUD success;
    class FAIL failure;
    class REVIEW,MANUAL review;
```

------------------------------------------------------------------------

# 24. Complete "Happy Path"

This is the cleanest path through the platform.

``` mermaid
flowchart LR
    A([Company User]) --> B[Register / Login]
    B --> C[Company Workspace]
    C --> D[Create / Import Customers]
    D --> E[Upload Invoice]
    E --> F[OCR / Validate]
    F --> G[Invoice Stored]
    G --> H[Upload Bank Statement]
    H --> I[Import Payment]
    I --> J[Identify Payer]
    J --> K[Find Outstanding Invoices]
    K --> L[Generate Match Candidates]
    L --> M[Collect Evidence]
    M --> N[Calculate Deterministic Score]
    N --> O[Decision]
    O --> P[Show Suggestion]
    P --> Q[Accountant Approves]
    Q --> R[Atomic Financial Update]
    R --> S[Invoice Balance / Status Updated]
    S --> T[Audit Event]
    T --> U[Dashboard Updated]
    U --> V([Complete])

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;

    class A actor;
    class B,C,D,E,F,G,H,I,J,K,L,M,N,O,P,Q,R,S,T,U process;
    class N,R work;
    class V success;
```

------------------------------------------------------------------------

# 25. Complete "Failure-Aware" Path

This is the more important production behavior: failure does not mean
corrupted state.

``` mermaid
flowchart LR
    START([Any Operation]) --> EXEC[Execute operation]
    EXEC --> OK{Successful?}

    OK -->|YES| COMMIT[Persist valid state]
    COMMIT --> DONE([Continue workflow])

    OK -->|NO| CLASSIFY{Failure classified}

    CLASSIFY -->|Validation| USERFIX[Show actionable error]
    USERFIX --> RETRYUSER[User corrects input]
    RETRYUSER --> EXEC

    CLASSIFY -->|Transient dependency| SAFE{Safe to retry?}
    SAFE -->|YES| BACKOFF[Bounded retry + backoff]
    BACKOFF --> EXEC
    SAFE -->|NO| PENDING[Recoverable pending state]

    CLASSIFY -->|Concurrency| STALE[Reject stale operation]
    STALE --> REFRESH[Refresh current state]
    REFRESH --> REVIEW[Human / workflow retry]
    REVIEW --> EXEC

    CLASSIFY -->|Duplicate| IDEMP[Return existing result / flag duplicate]
    IDEMP --> DONE

    CLASSIFY -->|Permanent business rule| MANUAL[Needs Review]
    MANUAL --> HUMAN[Human resolves]
    HUMAN --> EXEC

    CLASSIFY -->|Unexpected| FAIL[Failed — preserve state + log + alert]
    FAIL --> RECOVERY[Recovery workflow]
    RECOVERY --> EXEC

    classDef actor fill:#1467b3,stroke:#58a6ff,color:#fff,stroke-width:2px;
    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class START actor;
    class EXEC,COMMIT,USERFIX,RETRYUSER,BACKOFF,PENDING,STALE,REFRESH,REVIEW,IDEMP,MANUAL,HUMAN,FAIL,RECOVERY process;
    class OK,CLASSIFY,SAFE decision;
    class DONE success;
    class FAIL failure;
    class PENDING,REVIEW,MANUAL,HUMAN review;
```

------------------------------------------------------------------------

# 26. Final System State Model

The system should not hide failure. A recoverable state is a valid
operational state.

``` mermaid
flowchart TB
    subgraph Invoice
        I1[RECEIVED] --> I2[PROCESSING]
        I2 --> I3[EXTRACTED / VALIDATED]
        I2 --> I4[RETRY_WAIT]
        I4 --> I2
        I2 --> I5[NEEDS_REVIEW]
        I3 --> I6[PENDING]
        I6 --> I7[PARTIAL]
        I7 --> I6
        I6 --> I8[PAID]
        I6 --> I9[CANCELLED]
    end

    subgraph Payment
        P1[IMPORTED] --> P2[UNRECONCILED]
        P2 --> P3[RECONCILING]
        P3 --> P4[MATCHED / SUGGESTED]
        P3 --> P5[AMBIGUOUS]
        P3 --> P6[UNMATCHED]
        P4 --> P7[REVIEW]
        P5 --> P7
        P6 --> P7
        P7 --> P8[APPROVED]
        P7 --> P9[REJECTED / DEFERRED]
        P8 --> P10[ALLOCATED]
    end

    subgraph Recovery
        R1[RETRY_PENDING] --> R2[RETRY]
        R2 --> R3[RECOVERED]
        R2 --> R4[EXHAUSTED]
        R4 --> R5[FAILED_REVIEWABLE]
    end

    classDef process fill:#1d64a5,stroke:#5da9e9,color:#fff;
    classDef work fill:#c99a18,stroke:#f2d36b,color:#111;
    classDef decision fill:#d67b16,stroke:#f4b95f,color:#fff;
    classDef success fill:#128c7e,stroke:#43d8c6,color:#fff,stroke-width:2px;
    classDef failure fill:#b0003a,stroke:#f05b8a,color:#fff;
    classDef review fill:#9e1748,stroke:#ee6c9d,color:#fff;

    class I1,I2,I3,I4,I6,I7,P1,P2,P3,P4,P5,P6,P8,P9,R1,R2,R3 process;
    class I5,P7,R5 review;
    class I8,I9,P10,R3 success;
    class R4 failure;
```

------------------------------------------------------------------------

# 27. What This Flow Guarantees

This document defines the intended operational behavior, not a claim
that software can literally "never fail."

The engineering objective is:

``` text
Failure happens
      ↓
Failure is represented explicitly
      ↓
State remains consistent
      ↓
Unsafe mutation is prevented
      ↓
Safe retry happens when possible
      ↓
Otherwise recovery state is created
      ↓
Human can resolve unresolved cases
      ↓
Important actions are auditable
```

## Non-negotiable financial rules

1.  **AI does not make the financial decision.**
2.  **AI does not calculate financial confidence.**
3.  **AI does not allocate payments.**
4.  **AI does not directly mutate invoice financial state.**
5.  **Reconciliation proposes; controlled financial update mutates.**
6.  **Financial mutations are atomic.**
7.  **Duplicate operations are idempotent.**
8.  **Concurrent stale approvals are rejected safely.**
9.  **Insufficient or conflicting evidence goes to review.**
10. **Failures are visible and recoverable.**
11. **Audit history is recorded for important actions.**
12. **A heuristic score is not presented as a calibrated probability.**
13. **Candidate truncation is explicitly recorded.**
14. **Optional dependency failure must not silently corrupt core
    financial state.**
15. **No infinite blind retries.**

------------------------------------------------------------------------

# 28. Product Boundary

## MVP

``` text
Authentication
Company Workspace
Customer Management
Invoice Upload / Management
OCR
CSV Bank Statement Upload
Payment Import
Deterministic Reconciliation
Review Center
Dashboard
Audit Logs
Settings
```

## Future

``` text
Gmail
Outlook
Zoho Books
QuickBooks
Tally
ERP integrations
Live Bank APIs
Payment Gateways
AI Reminder Emails
AI Cash Flow Insights
AI Anomaly Detection
Multi-Currency
Enterprise Readiness
```

The MVP remains an **AI Invoice Reconciliation Platform**, not a
replacement accounting/ERP system.

------------------------------------------------------------------------

# 29. One-Line Mental Model

``` text
IMPORT
  ↓
VALIDATE
  ↓
IDENTIFY
  ↓
GENERATE CANDIDATES
  ↓
MATCH
  ↓
COLLECT EVIDENCE
  ↓
DECIDE
  ↓
REVIEW WHEN UNCERTAIN
  ↓
APPROVE / MANUALLY ALLOCATE
  ↓
ATOMIC FINANCIAL UPDATE
  ↓
AUDIT
  ↓
DASHBOARD
  ↓
COMPLETE
```

**And whenever something goes wrong:**

``` text
FAIL
 ↓
CLASSIFY
 ↓
RETRY IF SAFE
 ↓
OTHERWISE PRESERVE STATE
 ↓
RECOVERY / HUMAN REVIEW
```
