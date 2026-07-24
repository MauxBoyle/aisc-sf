"""Salesforce picklist API values that the Python application understands.

This is deliberately a small catalog.  It records values used in the project's
queries, decisions, and writes; it is not intended to copy every value that an
administrator has configured in Salesforce.
"""

from __future__ import annotations

from enum import StrEnum


class AccountCertificationStatus(StrEnum):
    """Certification statuses used by the application."""

    INITIALS = "Initials"
    CERTIFIED = "Certified"
    DROPPED = "Dropped"
    SUSPENDED = "Suspended" #It is unclear what our best practice is for using this option -MB 7/24/26


class AccountHistoryField(StrEnum):
    """Account fields that can appear in AccountHistory records."""

    BILLING_CITY = "BillingCity"
    BILLING_COUNTRY = "BillingCountry"
    BILLING_GEOCODE_ACCURACY = "BillingGeocodeAccuracy"
    BILLING_LATITUDE = "BillingLatitude"
    BILLING_LONGITUDE = "BillingLongitude"
    BILLING_POSTAL_CODE = "BillingPostalCode"
    BILLING_STATE = "BillingState"
    BILLING_STREET = "BillingStreet"
    CERT_ACCOUNTING_CONTACT = "Cert_Accounting_Contact__c"
    CERT_AUDIT_DURATION = "Cert_Audit_Duration__c"
    CERT_AUDIT_PACKAGE = "Cert_Audit_Package__c"
    CERT_CERTIFICATION_CONTACT = "Cert_Certification_Contact__c"
    CERT_CERTIFICATION_STATUS = "Cert_Certification_Status__c"
    CERT_MARKETING_CONTACT = "Cert_Marketing_Contact__c"
    CERT_NOTES = "Cert_Notes__c"
    CERT_PRINCIPAL_CONTACT = "Cert_Principal_Contact__c"
    CERT_SCHEDULING_2_0_CERT_MONTH = "Cert_Scheduling_2_0_Cert_Month__c"
    ENGAGEMENT_STAGE = "Engagement_Stage__c"
    IMISID = "IMISID__c"
    INDUSTRY = "Industry"
    IS_ACTIVE_IN_IMIS_FOR_MEMBER_DISCOUNT = (
        "Is_active_in_IMIS_for_Member_Discount__c"
    )
    NY_PROGRAM_PARTICIPANT = "NY_Program_Participant__c"
    NUMBER_OF_EMPLOYEES = "NumberOfEmployees"
    OWNER = "Owner"
    SHIPPING_CITY = "ShippingCity"
    SHIPPING_COUNTRY = "ShippingCountry"
    SHIPPING_POSTAL_CODE = "ShippingPostalCode"
    SHIPPING_STATE = "ShippingState"
    SHIPPING_STREET = "ShippingStreet"
    TEXT_NAME = "TextName"
    WEBSITE = "Website"
    ACCOUNT_CREATED_FROM_LEAD = "accountCreatedFromLead"
    ACCOUNT_MERGED = "accountMerged"
    ACCOUNT_UPDATED_BY_LEAD = "accountUpdatedByLead"
    CREATED = "created"


class CaseCertificationStage(StrEnum):
    """Certification Case stages used by application-stage decisions."""

    CANCEL = "Cancel"
    NEW_APPLICATION = "New_Application"
    DOC_AUDIT = "Doc_Audit"
    PENDING_AUDIT_ASSIGNMENT = "Pending_AuditAssignment"
    DOC_AUDIT_LABEL = "Doc Audit"
    ELIGIBILITY_REVIEW = "Eligibility Review"
    INITIAL_REVIEW = "Initial Review"


class ScopeChangeAnswer(StrEnum):
    """Scope-change answers used by the application filter."""

    YES = "Yes"
    NO = "No"


class AuditStatus(StrEnum):
    """Audit statuses used by application-stage decisions."""

    CANCELED = "Canceled"
    WITHDRAWN = "Withdrawn"
    PENDING_ACCEPTANCE = "Pending Acceptance"
    RESCHEDULE_IN_PROGRESS = "Reschedule in Progress"
    COMPLETED_CONDITIONAL = "Completed - Conditional"
    COMPLETED_DENIED = "Completed - Denied"
    COMPLETED_GRANTED = "Completed - Granted"
    COMPLETED_UNDER_REVIEW = "Completed - Under Review"
    LEGACY = "Legacy"
    NEW = "New"
    RESCHEDULE_NEEDED = "Reschedule Needed"
    SCHEDULED_APPROVED = "Scheduled - Approved"


class AuditType(StrEnum):
    """Audit types excluded from the application snapshot."""

    ADDITIONAL = "Additional"
    APPEAL = "Appeal"
    SA_NYC = "SA-NYC"
    PREASSESSMENT = "Preassessment"
    B_AUDIT_CAR = "B Audit CAR"
    B_AUDIT_WORK = "B Audit Work"
    INITIAL = "Initial"
    LEGACY = "Legacy"
    MULTI_SITE_CSE = "Multi-Site CSE"
    MULTI_SITE_CX = "Multi-Site CX"
    MULTI_SITE_DUAL = "Multi-Site Dual"
    MULTI_SITE_FA = "Multi-Site FA"
    RENEWAL = "Renewal"
    RESCHEDULE = "Reschedule"
    SCOPE_CHANGE = "Scope Change"
    SPLIT = "Split"


class ProfileChangeStatus(StrEnum):
    """Company Profile Change statuses read or written by the workflows."""

    NEW = "New"
    CLOSED = "Closed"


class ProfileChangeType(StrEnum):
    """Company Profile Change types used in Python decisions."""

    KEY_DATA = "Key Data"


class CompanyProfileChangeYesNo(StrEnum):
    """Yes/no answers on Company Profile Change records."""

    YES = "Yes"
    NO = "No"


class CaseStatus(StrEnum):
    """Case statuses written by the Profile Update workflows."""

    PENDING = "Pending"
    CLOSED = "Closed"
    AISC_STAFF = "AISC Staff"
    CARLO = "Carlo"
    CARLO_TO_REVIEW = "Carlo to Review"
    DESCH = "Desch"
    DRURY_TO_REVIEW = "Drury to Review"
    INBOX = "Inbox"
    LARRY = "Larry"
    NEW = "New"
    OPEN = "Open"
    RESOLVED = "Resolved"
    RESOLVED_INTERNAL = "Resolved - Internal"
    TRAVIS = "Travis"
    YASMIN = "Yasmin"
    YASMIN_TO_REVIEW = "Yasmin to Review"


class CaseOrigin(StrEnum):
    """Case origins written by the Profile Update automation."""

    WEB = "Web"
    PARTICIPANT_PORTAL = "Participant Portal"
    EMAIL = "Email"
    PHONE = "Phone"


class CaseLabel(StrEnum):
    """Case labels written by the Profile Update automation."""

    AUDITING = "Auditing"
    PARTICIPANT_PORTAL = "Participant Portal"
    AESS = "AESS"
    APPLICATION = "Application"
    APPLICATION_PERIOD = "Application."
    APPROVAL = "Approval"
    AUDIT = "Audit"
    AUDIT_PERIOD = "Audit."
    AWNINGS_AND_CANOPIES = "Awnings and Canopies"
    BENDING_STRAIGHTENING_AND_CAMBER = "Bending, Straightening and Camber"
    BLAST = "Blast"
    BOLTS_AND_WELDS_IN_COMBINATION = "Bolts and Welds in Combination"
    BRIDGES = "Bridges"
    BUILT_UP_MEMBERS = "Built-Up Members"
    COSP = "COSP"
    CASTING = "Casting"
    CERTIFICATE = "Certificate"
    CERTIFICATE_OF_INSURANCE = "Certificate of Insurance"
    CERTIFICATION = "Certification"
    COMBINED_STRESSES = "Combined Stresses"
    CONNECTIONS = "Connections"
    CONSTRUCTION = "Construction"
    CONSTRUCTION_AND_CONTROL_JOINTS = "Construction and Control Joints"
    CONTINUING_EDUCATION = "Continuing Education"
    CORROSION = "Corrosion"
    DETAILER_TRAINING = "Detailer Training"
    DETAILING = "Detailing"
    EMBEDS = "Embeds"
    ERECTION_BRACING = "Erection Bracing"
    FABRICATION = "Fabrication"
    FATIGUE = "Fatigue"
    FAÇADE = "Façade"
    FIELD_FIXES = "Field Fixes"
    FIRE = "Fire"
    HISTORICAL = "Historical"
    HOLLOW_CORE = "Hollow Core"
    I_DONT_KNOW = "I don't know."
    INSPECTION = "Inspection"
    INVOICING = "Invoicing"
    JOISTS = "Joists"
    LEED = "LEED"
    LADDERS = "Ladders"
    LATTICE_TOWERS = "Lattice Towers"
    LIFTING = "Lifting"
    LOGIN_ISSUES = "Login Issues"
    MAGAZINE_SUBSCRIPTIONS = "Magazine Subscriptions"
    MARKING = "Marking"
    MATERIALS = "Materials"
    MEMBER_DESIGN = "Member Design"
    MEMBERSHIP = "Membership"
    MEMBERSHIP_APPLICATION = "Membership Application"
    MEMBERSHIP_CANCELLATION = "Membership Cancellation"
    MEMBERSHIP_RENEWAL = "Membership Renewal"
    MEMBERSHIP_RENEWALS = "Membership Renewals"
    METAL_BUILDINGS = "Metal Buildings"
    MISC_METALS = "Misc Metals"
    NRN = "NRN"
    NRN_PERIOD = "NRN."
    NO_RESPONSE_NEEDED = "No Response Needed"
    NO_RESPONSE_NEEDED_PERIOD = "No Response Needed."
    NUCLEAR = "Nuclear"
    OSHA = "OSHA"
    OTHER_HIGH = "Other - high"
    OTHER_HIGH_PERIOD = "Other - high."
    OTHER_HIGH_COMPACT = "OtherHigh"
    PAINTING_AND_SURFACE_PREP = "Painting and Surface Prep"
    PARKING_STRUCTURES = "Parking Structures"
    PHONE_CALLS = "Phone Calls"
    PONDING = "Ponding"
    PROFILE_CHANGE = "Profile Change"
    PROGRAM_INQUIRY = "Program Inquiry"
    PROTECTED_ZONE = "Protected Zone"
    PUBLICATIONS = "Publications"
    REQUEST_FOR_SPEAKERS = "Request for Speakers"
    ROSTER_UPDATES = "Roster Updates"
    SAFETY = "Safety"
    SCHEDULING = "Scheduling"
    SHIPPING = "Shipping"
    SILOS_BINS_HOPPERS = "Silos, Bins, Hoppers"
    SLAB_EDGE = "Slab Edge"
    SOFTWARE_ISSUES = "Software Issues"
    SOUND = "Sound"
    SPEEDCORE = "Speedcore"
    STABILITY = "Stability"
    STAIRS = "Stairs"
    STEEL_DECK = "Steel Deck"
    STEEL_MAKING = "Steel Making"
    STEEL_PRICES = "Steel Prices"
    SUBSCRIPTION = "Subscription"
    SUBSCRIPTION_INQUIRY = "Subscription Inquiry"
    TECHNICAL_INQUIRY = "Technical Inquiry"
    TOLERANCES = "Tolerances"
    TONNAGE = "Tonnage"
    VIBRATION = "Vibration"
    WEBSITE_ISSUES = "Website Issues"
    WELDING = "Welding"


class CaseSubLabel(StrEnum):
    """Case sub-labels written by the Profile Update automation."""

    PROFILE_CHANGE = "Profile Change"
    ADDAMEMBER = "Addamember"
    ADDSUBSCRIPTION = "Addsubscription"
    ANGLES_IN_FLEXURE = "Angles in Flexure"
    ANNUAL_DUES = "Annual Dues"
    APPLICATION_CERTIFICATION = "Application Certification"
    AUDIT_DATE = "Audit Date"
    BASE_METAL_STRENGTH = "Base Metal Strength"
    BEARING_JOINTS = "Bearing Joints"
    BENT_ANCHOR_RODS = "Bent Anchor Rods"
    BOLT_BANGING = "Bolt Banging"
    BOLTING = "Bolting"
    BOX_GIRDERS = "Box Girders"
    BUILT_UP_COMPRESSION = "Built-Up Compression"
    BUILT_UP_FLEXURAL = "Built-Up Flexural"
    COVID = "COVID"
    CRG_REVIEW = "CRG Review"
    CVN = "CVN"
    CABLES = "Cables"
    CAST_IRON = "Cast Iron"
    CASTELLATED_BEAMS = "Castellated Beams"
    CERT_LOGO_REQUEST = "Cert Logo Request"
    CERTIFICATE_OF_INSURANCE = "Certificate of Insurance"
    CHAPTER_N = "Chapter N"
    CLEARANCES = "Clearances"
    COLUMN = "Column"
    COMPACTNESS = "Compactness"
    COMPOSITE_BEAMS = "Composite Beams"
    CONTACT_CHANGE = "ContactChange"
    COPES = "Copes"
    COVER_PLATES = "Cover Plates"
    CRUCIFORM = "Cruciform"
    CURVED_MEMBERS = "Curved Members"
    D1_8 = "D1.8"
    DEFLECTIONS = "Deflections"
    DISTORTION = "Distortion"
    DUCT_WORK = "Duct Work"
    ECCENTRICITY = "Eccentricity"
    ELEVATED_TEMPERATURE = "Elevated Temperature"
    EMBEDS = "Embeds"
    EXPANSION_JOINTS = "Expansion Joints"
    FEA = "FEA"
    FEEDBACK = "Feedback"
    FLANGE_BENDING = "Flange Bending"
    FOREIGN_STEEL = "Foreign Steel"
    FULL_MEMBERSHIP = "FullMembership"
    GALVANIZED = "Galvanized"
    GIRTS = "Girts"
    HSS = "HSS"
    HISTORICAL = "Historical"
    HISTORY = "History"
    HOLE_SIZES = "Hole Sizes"
    IAS = "IAS"
    LADDERS = "Ladders"
    LEVELLING_NUTS = "Levelling Nuts"
    LIFTING_BEAMS = "Lifting Beams"
    LINTELS = "Lintels"
    LOCK_WASHERS = "Lock Washers"
    LOGIN_ISSUES = "LoginIssues"
    LOW_TEMPERATURE = "Low Temperature"
    MAXIMUM_SPACING = "Maximum Spacing"
    MAXIMUM_WELD_SIZE = "Maximum Weld Size"
    MISLOCATED_HOLES = "Mislocated Holes"
    MOMENT_CONNECTIONS = "Moment Connections"
    N690 = "N690"
    NAVIGATING_PORTAL = "Navigating Portal"
    ONE_SIDED = "One sided"
    PAY_INVOICE = "PayInvoice"
    PILING = "Piling"
    PIN_CONNECTIONS = "Pin Connections"
    PINS = "Pins"
    PIPE_SUPPORT = "Pipe Support"
    PLATE_GIRDERS = "Plate Girders"
    PREINSTALLATION_VERIFICATION = "Preinstallation Verification"
    PRETENSION = "Pretension"
    PROFESSIONAL_MEMBERSHIP = "ProfessionalMembership"
    PRYING = "Prying"
    QUALIFICATION = "Qualification"
    REBAR = "Rebar"
    RECYCLED_CONTENT = "Recycled Content"
    REINFORCING = "Reinforcing"
    RESCHEDULE_REQUEST = "Reschedule Request"
    RESEND_INVOICES = "ResendInvoices"
    RESET_CREDENTIALS = "ResetCredentials"
    RIVETS = "Rivets"
    ROTATIONAL_DUCTILITY = "Rotational Ductility"
    SAFETY = "Safety"
    SEAL_WELDING = "Seal Welding"
    SEISMIC = "Seismic"
    SHEAR_CONNECTIONS = "Shear Connections"
    SILICON_STEEL = "Silicon Steel"
    SINGLE_ANGLES = "Single Angles"
    SPRAY_ON = "Spray On"
    STABILITY_BRACING = "Stability Bracing"
    STAGE_1_2_AND_CRG_REVIEW = "Stage 1, 2, & CRG Review"
    STAGE_2_REVIEW = "Stage 2 Review"
    STAINLESS = "Stainless"
    STANDARD = "Standard"
    STEEL_PLATE_SHEAR_WALLS = "Steel Plate Shear Walls"
    STRUCTURAL_INTEGRITY = "Structural Integrity"
    STUDS = "Studs"
    SUBMIT_TONNAGE_REPORT = "Submittonnagereport"
    TABLE_B4 = "Table B4"
    TENSION_ONLY = "Tension Only"
    THERMAL_BREAKS = "Thermal Breaks"
    THICK_PLATES = "Thick Plates"
    THREAD_ENGAGEMENT = "Thread Engagement"
    THREADS_EXCLUDED = "Threads Excluded"
    TORSION = "Torsion"
    TRACEABILITY = "Traceability"
    TRUSSES = "Trusses"
    U_BOLTS = "U Bolts"
    UFM = "UFM"
    UNCOPED_BEAMS = "Uncoped Beams"
    VERTICAL_BRACE_CONNECTION = "Vertical Brace Connection"
    WASHERS = "Washers"
    WEATHERING_STEEL = "Weathering Steel"
    WEB_OPENINGS = "Web Openings"
    X_BRACE = "X Brace"


# An explicit mapping makes catalog coverage easy to review in one place.
SALESFORCE_ENUMS: dict[tuple[str, str], type[StrEnum]] = {
    ("Account", "Cert_Certification_Status__c"): AccountCertificationStatus,
    ("AccountHistory", "Field"): AccountHistoryField,
    ("Case", "Cert_Stage__c"): CaseCertificationStage,
    ("Case", "Cert_Is_this_a_scope_change__c"): ScopeChangeAnswer,
    ("Case", "Status"): CaseStatus,
    ("Case", "Origin"): CaseOrigin,
    ("Case", "Label_new__c"): CaseLabel,
    ("Case", "Sub_Label__c"): CaseSubLabel,
    ("Cert_Audit__c", "Cert_Audit_Status__c"): AuditStatus,
    ("Cert_Audit__c", "Cert_Audit_Type__c"): AuditType,
    ("Company_Profile_Change__c", "Status__c"): ProfileChangeStatus,
    ("Company_Profile_Change__c", "Type__c"): ProfileChangeType,
    (
        "Company_Profile_Change__c",
        "Did_the_Cert_contact_change__c",
    ): CompanyProfileChangeYesNo,
    (
        "Company_Profile_Change__c",
        "Did_the_executive_manager_change__c",
    ): CompanyProfileChangeYesNo,
    (
        "Company_Profile_Change__c",
        "Existing_equipment_moved_to_new_facility__c",
    ): CompanyProfileChangeYesNo,
    (
        "Company_Profile_Change__c",
        "Will_QMS_or_documentation_change__c",
    ): CompanyProfileChangeYesNo,
    (
        "Company_Profile_Change__c",
        "Will_new_equipment_be_purchased__c",
    ): CompanyProfileChangeYesNo,
    (
        "Company_Profile_Change__c",
        "Will_old_equipment_be_removed__c",
    ): CompanyProfileChangeYesNo,
    (
        "Company_Profile_Change__c",
        "Will_software_change__c",
    ): CompanyProfileChangeYesNo,
    (
        "Company_Profile_Change__c",
        "Will_you_change_personnel__c",
    ): CompanyProfileChangeYesNo,
}
