"""API contract constants."""

KNOWN_INPUT_FIELDS = frozenset({
    "taggingai",
    "tagnumbercheck",
    "assetnamedescriptionmatch",
    "assetmakemodelmatch",
    "acquisitiondatecostmatch",
    "assetcondition",
    "assetid", "assettaggingdetailid", "customerid", "companyid",
    "assetclassid", "categoryid", "subcategoryid", "makemodelid",
    "assetname", "description", "tagnumber", "assetnumber",
    "assetclassname", "categoryname", "subcategoryname",
    "makemodelname", "makemodel", "model", "modelid", "classnumber",
    "company",
    "assetimage", "barcodeimage",
    "cost", "acquisitioncost", "acquisitiondate", "acquisition_date",
})

CHECK_FLAGS = (
    "tagnumbercheck",
    "assetnamedescriptionmatch",
    "assetmakemodelmatch",
    "acquisitiondatecostmatch",
    "assetcondition",
)

# Request fields echoed in the response as-is (never nulled when a check is disabled).
USER_ECHO_KEYS = frozenset({
    "tagnumber",
    "assetname",
    "description",
    "categoryid",
    "categoryname",
    "subcategoryid",
    "subcategoryname",
    "makemodelid",
    "makemodel",
    "modelid",
    "model",
    "acquisitiondate",
    "cost",
})

CHECK_OUTPUT_KEYS: dict[str, frozenset[str]] = {
    "tagnumbercheck": frozenset({
        "detectedtagnumber",
        "detectedtagnumbermatch",
        "detectedtagnumbermatchpercent",
        "decodedtagnumber",
        "tagnumbersource",
        "barcodeposition",
        "barcodereadability",
        "tag_number_status",
    }),
    "assetnamedescriptionmatch": frozenset({
        "imageAnalysis",
        "detectedAsset",
        "detected_description",
        "namedescriptionmatch",
        "namedescriptionmatchpercent",
        "reasoning",
        "imageReadability",
    }),
    "assetmakemodelmatch": frozenset({
        "subcatmodelmatch",
        "subcatmodelmatchpercent",
        "subcatmodelreasoning",
        "recommendedcategory",
        "recommendedsubcategory",
        "recommendedmakemodel",
        "makemodelmatch",
        "makemodelmatchpercent",
        "recommendedmodel",
        "categorymatch",
        "categorymatchpercent",
        "subcategorymatch",
        "subcategorymatchpercent",
        "modelmatch",
        "modelmatchpercent",
        "detected_description",
        "imageReadability",
    }),
    "acquisitiondatecostmatch": frozenset({
        "currency",
        "cost_lower_bound",
        "cost_upper_bound",
        "cost_acceptance_lower",
        "cost_acceptance_upper",
        "cost_range",
        "cost_check",
        "model_release_year",
        "model_end_year",
        "acquisition_date_valid",
        "detected_description",
        "reasoning",
    }),
    "assetcondition": frozenset({
        "condition",
        "detected_description",
        "imageReadability",
    }),
}

# Fields set by more than one check — not nulled when a single check is disabled.
SHARED_CHECK_OUTPUT_KEYS = frozenset({
    "imageReadability",
    "detected_description",
    "imageAnalysis",
    "detectedAsset",
    "reasoning",
})

# Canonical response field order (used for serialization: populated first, nulls last).
RESPONSE_KEY_ORDER = (
    "taggingai",
    "assetid",
    "assettaggingdetailid",
    "customerid",
    "companyid",
    "assetclassid",
    "assetclassname",
    "classnumber",
    "tagnumber",
    "detectedtagnumber",
    "detectedtagnumbermatch",
    "detectedtagnumbermatchpercent",
    "decodedtagnumber",
    "tagnumbersource",
    "barcodeposition",
    "barcodereadability",
    "assetname",
    "description",
    "imageAnalysis",
    "detectedAsset",
    "detected_description",
    "namedescriptionmatch",
    "namedescriptionmatchpercent",
    "reasoning",
    "categoryid",
    "categoryname",
    "subcategoryid",
    "subcategoryname",
    "makemodelid",
    "makemodel",
    "modelid",
    "model",
    "subcatmodelmatch",
    "subcatmodelmatchpercent",
    "subcatmodelreasoning",
    "recommendedcategory",
    "recommendedsubcategory",
    "recommendedmakemodel",
    "makemodelmatch",
    "makemodelmatchpercent",
    "recommendedmodel",
    "categorymatch",
    "categorymatchpercent",
    "subcategorymatch",
    "subcategorymatchpercent",
    "modelmatch",
    "modelmatchpercent",
    "cost",
    "acquisitiondate",
    "currency",
    "cost_lower_bound",
    "cost_upper_bound",
    "cost_acceptance_lower",
    "cost_acceptance_upper",
    "cost_range",
    "cost_check",
    "model_release_year",
    "model_end_year",
    "acquisition_date_valid",
    "condition",
    "imageReadability",
    "finalverdict",
    "finalverdict_reason",
    "msg",
    "llm_provider",
    "error",
)

ALL_RESPONSE_KEYS = frozenset(RESPONSE_KEY_ORDER)

ALWAYS_RESPONSE_KEYS = frozenset({
    "taggingai",
    "assetid",
    "assettaggingdetailid",
    "customerid",
    "companyid",
    "assetclassid",
    "assetclassname",
    "classnumber",
    *USER_ECHO_KEYS,
    "finalverdict",
    "finalverdict_reason",
    "msg",
    "llm_provider",
    "error",
})

DANGEROUS_RESPONSE_KEYS = frozenset({
    "assetid", "assettaggingdetailid", "customerid", "companyid", "error", "success",
})

LLM_NULL_STRINGS = frozenset({
    "none", "null", "n/a", "na", "nil", "unknown", "not visible",
    "not found", "no stickers", "no sticker", "no labels", "no label",
    "not applicable", "unreadable", "",
})

MAX_CUSTOM_FIELDS = 10

# Numeric values that appeared in tag-reading prompts as format examples only.
# Reject if the model echoes them instead of reading from the image.
PROMPT_EXAMPLE_TAG_NUMBERS = frozenset({
    "1000063964",
    "1000102034",
    "1000063677",
    "1000000904",
    "1000000902",
    "7890123456",
})
