"""Plain-language descriptions of common lab tests.

General information about what each test measures and why it is usually ordered, shown on a
test's page when the user hasn't written their own description. It is not medical advice and
does not interpret individual results.
"""

import re
from typing import Dict, Optional, Tuple

# (description, aliases). Aliases are matched after normalizing case, punctuation and the words
# "serum", "plasma", "blood" and "level", so "Cholesterol, Total" and "Total Cholesterol" match.
_TESTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    # Metabolic panel
    ("Glucose is the main sugar in your blood and the body's main source of energy. It is checked to screen for and monitor diabetes; fasting before the test makes the result easier to compare.",
     ("glucose", "fasting glucose", "glucose fasting", "glu")),
    ("Blood urea nitrogen (BUN) is a waste product made when the body breaks down protein. The kidneys clear it, so it is used with creatinine to check kidney function and hydration.",
     ("bun", "urea nitrogen", "blood urea nitrogen", "urea nitrogen bun")),
    ("Creatinine is a waste product from normal muscle activity that the kidneys filter out. It is one of the main tests of how well the kidneys are working.",
     ("creatinine", "creat")),
    ("Estimated glomerular filtration rate (eGFR) is calculated from creatinine, age and sex to estimate how much blood the kidneys filter each minute. It is used to stage kidney function.",
     ("egfr", "egfr if nonafricn am", "egfr non african american", "egfr african american", "estimated gfr", "gfr estimated")),
    ("The BUN-to-creatinine ratio compares two kidney-related waste products. It can help tell whether a change in kidney tests is related to hydration or to the kidneys themselves.",
     ("bun creatinine ratio", "bun to creatinine ratio", "bun creat ratio")),
    ("Sodium is an electrolyte that helps control fluid balance, nerves and muscles. It is part of routine metabolic panels.",
     ("sodium", "na")),
    ("Potassium is an electrolyte essential for heart rhythm, nerves and muscles. Some medicines and kidney conditions change it, so it is checked routinely.",
     ("potassium", "k")),
    ("Chloride is an electrolyte that works with sodium to keep fluids and acid-base balance steady.",
     ("chloride", "cl")),
    ("Carbon dioxide (bicarbonate) reflects the acid-base balance of the blood, which the lungs and kidneys regulate.",
     ("carbon dioxide", "carbon dioxide total", "co2", "bicarbonate", "total co2")),
    ("Calcium is a mineral needed for bones, muscles, nerves and the heart. Blood levels are kept in a narrow range by the parathyroid glands and vitamin D.",
     ("calcium", "ca")),
    ("Total protein measures all the protein in the blood, mainly albumin and globulins. It gives a general picture of nutrition, liver and kidney health.",
     ("protein total", "total protein")),
    ("Albumin is the most common blood protein, made by the liver. It keeps fluid in the blood vessels and carries hormones and medicines; it reflects liver function and nutrition.",
     ("albumin",)),
    ("Globulins are a group of blood proteins that include antibodies. The value is usually calculated as total protein minus albumin.",
     ("globulin", "globulin total")),
    ("The albumin-to-globulin (A/G) ratio compares the two main groups of blood proteins.",
     ("a g ratio", "albumin globulin ratio", "ag ratio")),
    ("Bilirubin is a yellow substance made when red blood cells break down; the liver processes it. It is used to check liver and gallbladder function.",
     ("bilirubin total", "total bilirubin", "bilirubin")),
    ("Direct (conjugated) bilirubin is the part of bilirubin the liver has already processed. It helps narrow down the cause of a high total bilirubin.",
     ("bilirubin direct", "direct bilirubin")),
    ("Alkaline phosphatase (ALP) is an enzyme found mainly in the liver and bones. It is used to check liver and bone health.",
     ("alkaline phosphatase", "alk phos", "alp")),
    ("AST (aspartate aminotransferase) is an enzyme found in the liver, heart and muscles. It is part of liver function testing.",
     ("ast", "ast sgot", "sgot", "aspartate aminotransferase")),
    ("ALT (alanine aminotransferase) is an enzyme found mostly in the liver. It is one of the most specific routine tests for liver inflammation.",
     ("alt", "alt sgpt", "sgpt", "alanine aminotransferase")),
    ("GGT (gamma-glutamyl transferase) is a liver enzyme. It helps work out whether a raised alkaline phosphatase comes from the liver or the bones, and is affected by alcohol.",
     ("ggt", "gamma glutamyl transferase", "gamma gt")),
    ("Magnesium is a mineral involved in muscle, nerve and heart function.",
     ("magnesium", "mg")),
    ("Phosphorus (phosphate) is a mineral that works with calcium to build bones and teeth.",
     ("phosphorus", "phosphate", "phosphorus inorganic")),
    ("Uric acid is a waste product from breaking down purines found in some foods. High levels are linked to gout and kidney stones.",
     ("uric acid",)),
    # Lipids
    ("Total cholesterol is all the cholesterol in your blood, including LDL and HDL. It is used with the other lipid values to estimate heart and blood vessel risk.",
     ("cholesterol total", "total cholesterol", "cholesterol")),
    ("LDL cholesterol is often called \"bad\" cholesterol because higher levels can build up in artery walls. It is the main lipid target for heart disease prevention.",
     ("ldl cholesterol", "ldl chol calc nih", "ldl chol calc", "ldl calc", "ldl", "ldl c", "ldl cholesterol calc", "ldl direct")),
    ("HDL cholesterol is often called \"good\" cholesterol because it helps carry cholesterol away from the arteries to the liver.",
     ("hdl cholesterol", "hdl", "hdl c")),
    ("Triglycerides are a type of fat in the blood that the body uses for energy. Levels rise after eating, so the test is often done fasting.",
     ("triglycerides", "triglyceride", "trig")),
    ("VLDL cholesterol carries mostly triglycerides in the blood. It is usually estimated from the triglyceride value.",
     ("vldl cholesterol", "vldl cholesterol cal", "vldl")),
    ("Non-HDL cholesterol is total cholesterol minus HDL, covering all the cholesterol types that can build up in arteries.",
     ("non hdl cholesterol", "non hdl", "non hdl c")),
    ("The total cholesterol-to-HDL ratio compares overall cholesterol with protective HDL; it is sometimes used in heart risk estimates.",
     ("cholesterol hdl ratio", "chol hdlc ratio", "t chol hdl ratio", "total cholesterol hdl ratio")),
    ("Apolipoprotein B (ApoB) counts the particles that carry \"bad\" cholesterol. It can add detail to heart risk beyond LDL alone.",
     ("apolipoprotein b", "apob", "apo b")),
    ("Lipoprotein(a) is an inherited type of cholesterol particle. Levels are mostly set by genetics and are linked to heart and blood vessel risk.",
     ("lipoprotein a", "lp a", "lpa")),
    # Diabetes
    ("Hemoglobin A1c shows your average blood sugar over roughly the past two to three months. It is used to screen for and monitor diabetes.",
     ("hemoglobin a1c", "hba1c", "a1c", "glycated hemoglobin", "glycohemoglobin")),
    ("Insulin is the hormone that moves sugar from the blood into cells. Measuring it can help assess insulin resistance.",
     ("insulin", "insulin fasting")),
    # Blood count
    ("White blood cells (WBC) fight infection. The count is part of a complete blood count and can change with infection, inflammation and some medicines.",
     ("wbc", "white blood cell count", "white blood cells", "leukocytes")),
    ("Red blood cells (RBC) carry oxygen from the lungs to the body. The count is part of a complete blood count.",
     ("rbc", "red blood cell count", "red blood cells", "erythrocytes")),
    ("Hemoglobin is the protein in red blood cells that carries oxygen. Low levels are the main sign of anemia.",
     ("hemoglobin", "hgb", "hb")),
    ("Hematocrit is the percentage of your blood made up of red blood cells.",
     ("hematocrit", "hct")),
    ("MCV (mean corpuscular volume) is the average size of your red blood cells. It helps identify the type of anemia, such as iron or B12 related.",
     ("mcv", "mean corpuscular volume")),
    ("MCH (mean corpuscular hemoglobin) is the average amount of hemoglobin in each red blood cell.",
     ("mch", "mean corpuscular hemoglobin")),
    ("MCHC is the average concentration of hemoglobin inside red blood cells.",
     ("mchc", "mean corpuscular hemoglobin concentration")),
    ("RDW (red cell distribution width) shows how much your red blood cells vary in size.",
     ("rdw", "rdw cv", "red cell distribution width")),
    ("Platelets are cell fragments that help blood clot.",
     ("platelets", "platelet count", "plt")),
    ("MPV (mean platelet volume) is the average size of your platelets.",
     ("mpv", "mean platelet volume")),
    ("Neutrophils are the most common white blood cells and the first responders to bacterial infection.",
     ("neutrophils", "neutrophils absolute", "neutrophils abs", "neutrophil", "neutrophils percent")),
    ("Lymphocytes are white blood cells that make antibodies and fight viral infections.",
     ("lymphocytes", "lymphs", "lymphs absolute", "lymphocytes absolute", "lymphs abs")),
    ("Monocytes are white blood cells that clear away dead cells and germs.",
     ("monocytes", "monocytes absolute", "monocytes abs")),
    ("Eosinophils are white blood cells involved in allergies and fighting parasites.",
     ("eosinophils", "eos", "eos absolute", "eosinophils absolute", "eos abs")),
    ("Basophils are the least common white blood cells and take part in allergic reactions.",
     ("basophils", "basos", "baso absolute", "basophils absolute", "baso abs")),
    # Iron and vitamins
    ("Ferritin reflects how much iron your body has stored. It is one of the best single tests for iron deficiency.",
     ("ferritin",)),
    ("Serum iron is the amount of iron circulating in the blood. It varies through the day, so it is read together with ferritin and TIBC.",
     ("iron", "iron total")),
    ("TIBC (total iron-binding capacity) measures how much iron the blood's carrier protein, transferrin, could hold. It rises when iron stores are low.",
     ("tibc", "total iron binding capacity", "iron binding capacity")),
    ("Iron saturation is the percentage of transferrin that is carrying iron.",
     ("iron saturation", "transferrin saturation", "iron saturation percent")),
    ("Vitamin B12 is needed to make red blood cells and keep nerves healthy.",
     ("vitamin b12", "b12", "cobalamin")),
    ("Folate (vitamin B9) is needed to make red blood cells and DNA.",
     ("folate", "folic acid", "folate rbc")),
    ("Vitamin D (25-hydroxy) is the best measure of the body's vitamin D supply, which helps absorb calcium for strong bones.",
     ("vitamin d", "vitamin d 25 hydroxy", "25 hydroxy vitamin d", "vitamin d 25 oh", "25 oh vitamin d")),
    # Thyroid
    ("TSH (thyroid-stimulating hormone) is made by the pituitary gland and tells the thyroid how much hormone to make. It is the main screening test for thyroid function.",
     ("tsh", "thyroid stimulating hormone")),
    ("Free T4 is the active, unbound form of the main thyroid hormone thyroxine. It is usually checked when TSH is outside its range.",
     ("free t4", "t4 free", "t4 free direct", "free thyroxine")),
    ("Free T3 is the active, unbound form of triiodothyronine, the most potent thyroid hormone.",
     ("free t3", "t3 free", "triiodothyronine free")),
    ("Total T4 measures all of the thyroxine in the blood, both bound to proteins and free.",
     ("t4", "thyroxine", "t4 thyroxine total", "thyroxine t4")),
    # Inflammation, hormones and other common tests
    ("C-reactive protein (CRP) is made by the liver in response to inflammation or infection.",
     ("c reactive protein", "crp")),
    ("High-sensitivity CRP measures low levels of inflammation and is sometimes used in heart risk assessment.",
     ("hs crp", "hscrp", "c reactive protein cardiac", "high sensitivity crp")),
    ("The sedimentation rate (ESR) measures how quickly red blood cells settle in a tube, a general sign of inflammation.",
     ("sed rate", "esr", "sedimentation rate", "sed rate by modified westergren")),
    ("PSA (prostate-specific antigen) is a protein made by the prostate. It is used to screen for and monitor prostate conditions.",
     ("psa", "prostate specific ag", "prostate specific antigen", "psa total")),
    ("Testosterone is the main male sex hormone, also present in smaller amounts in women.",
     ("testosterone", "testosterone total", "testosterone serum")),
    ("Cortisol is a stress hormone made by the adrenal glands. Levels change through the day, so the time of the test matters.",
     ("cortisol", "cortisol am")),
    ("The urine albumin-to-creatinine ratio checks for small amounts of protein leaking into the urine, an early sign of kidney damage.",
     ("albumin creatinine ratio", "microalbumin creatinine ratio", "urine albumin creatinine ratio", "uacr", "microalb creat ratio")),
    ("PT/INR measures how long blood takes to clot. INR is used to monitor blood thinners such as warfarin.",
     ("inr", "pt inr", "prothrombin time", "protime")),
    ("Creatine kinase (CK) is an enzyme found in muscles; it rises with muscle injury or strenuous exercise.",
     ("ck", "creatine kinase", "cpk", "creatine kinase total")),
    ("LDH (lactate dehydrogenase) is an enzyme found in most body tissues. It is a general marker of tissue damage.",
     ("ldh", "lactate dehydrogenase", "ld")),
)

_NOISE = {"serum", "plasma", "blood", "level", "levels", "s", "p", "b"}


def _normalize(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return " ".join(w for w in words if w not in _NOISE)


_INDEX: Dict[str, str] = {}
for _description, _aliases in _TESTS:
    for _alias in _aliases:
        _INDEX.setdefault(_normalize(_alias), _description)


def describe(name: str) -> Optional[str]:
    """Plain-language description of a lab test by name, or None if it isn't a known test."""
    key = _normalize(name)
    if not key:
        return None
    if key in _INDEX:
        return _INDEX[key]
    # Lab names sometimes carry a note in parentheses, e.g. "TSH (Thyroid Panel)". Urine tests are
    # never matched this way, so "Glucose (Urine)" doesn't get the blood glucose description.
    if "urine" in key.split():
        return None
    shorter = _normalize(re.sub(r"\(.*", "", name or ""))
    if shorter and shorter in _INDEX:
        return _INDEX[shorter]
    return None
