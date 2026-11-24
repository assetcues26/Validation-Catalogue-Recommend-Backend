"""Pydantic request/response schemas."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from asset_validator.core.constants import CHECK_FLAGS
from asset_validator.core.request_keys import normalize_request_keys
from asset_validator.core.validation_plan import ValidationPlan
from asset_validator.utils.date_utils import parse_acquisition_date


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


class AssetAnalysisRequest(BaseModel):
    """Validated TaggingAI API input — selective check flags required."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    taggingai: bool = Field(..., validation_alias=AliasChoices("taggingai", "TaggingAI"))
    tagnumbercheck: bool = Field(False, validation_alias=AliasChoices("tagnumbercheck", "TagNumberCheck"))
    assetnamedescriptionmatch: bool = Field(
        False, validation_alias=AliasChoices("assetnamedescriptionmatch", "AssetNameDescriptionMatch")
    )
    assetmakemodelmatch: bool = Field(
        False, validation_alias=AliasChoices("assetmakemodelmatch", "AssetMakeModelMatch")
    )
    acquisitiondatecostmatch: bool = Field(
        False, validation_alias=AliasChoices("acquisitiondatecostmatch", "AcquisitionDateCostMatch")
    )
    assetcondition: bool = Field(False, validation_alias=AliasChoices("assetcondition", "AssetCondition"))

    assetid: Optional[Any] = Field(None, validation_alias=AliasChoices("assetid", "AssetId"))
    assettaggingdetailid: Optional[Any] = Field(
        None, validation_alias=AliasChoices("assettaggingdetailid", "AssetTaggingDetailID")
    )
    customerid: Optional[Any] = Field(None, validation_alias=AliasChoices("customerid", "CustomerId"))
    companyid: Optional[Any] = Field(None, validation_alias=AliasChoices("companyid", "CompanyId"))
    assetclassid: Optional[Any] = Field(None, validation_alias=AliasChoices("assetclassid", "AssetClassId"))
    categoryid: Optional[Any] = Field(None, validation_alias=AliasChoices("categoryid", "CategoryId"))
    subcategoryid: Optional[Any] = Field(None, validation_alias=AliasChoices("subcategoryid", "SubCategoryId"))
    makemodelid: Optional[Any] = Field(None, validation_alias=AliasChoices("makemodelid", "MakeModelId"))
    modelid: Optional[Any] = Field(None, validation_alias=AliasChoices("modelid", "ModelId"))
    assetname: Optional[str] = Field(None, validation_alias=AliasChoices("assetname", "AssetName"))
    description: Optional[str] = Field(None, validation_alias=AliasChoices("description", "Description"))
    tagnumber: Optional[str] = Field(None, validation_alias=AliasChoices("tagnumber", "TagNumber"))
    assetnumber: Optional[str] = Field(None, validation_alias=AliasChoices("assetnumber", "AssetNumber"))
    assetclassname: Optional[str] = Field(
        None, validation_alias=AliasChoices("assetclassname", "AssetClassName")
    )
    classnumber: Optional[str] = Field(None, validation_alias=AliasChoices("classnumber", "ClassNumber"))
    categoryname: Optional[str] = Field(None, validation_alias=AliasChoices("categoryname", "CategoryName"))
    subcategoryname: Optional[str] = Field(
        None, validation_alias=AliasChoices("subcategoryname", "SubCategoryName")
    )
    makemodel: Optional[str] = Field(None, validation_alias=AliasChoices("makemodel", "MakeModel"))
    model: Optional[str] = Field(None, validation_alias=AliasChoices("model", "Model"))
    makemodelname: Optional[str] = Field(None, validation_alias=AliasChoices("makemodelname", "MakeModelName"))
    company: Optional[str] = Field(None, validation_alias=AliasChoices("company", "Company"))
    assetimage: Optional[str] = Field(None, validation_alias=AliasChoices("assetimage", "AssetImage"))
    barcodeimage: Optional[str] = Field(None, validation_alias=AliasChoices("barcodeimage", "BarcodeImage"))
    cost: Optional[float] = Field(
        None,
        ge=0,
        validation_alias=AliasChoices("cost", "acquisitioncost", "AcquisitionCost"),
        description="User-declared asset cost in the request currency (0 allowed; it fails the cost check)",
    )
    currency: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("currency", "Currency"),
        description="ISO-4217 code the declared cost is in; absent means INR",
    )
    acquisitiondate: Optional[date] = Field(
        None,
        validation_alias=AliasChoices("acquisitiondate", "acquisition_date", "AcquisitionDate"),
        description="Acquisition date DD-MM-YYYY or ISO",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_input_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return normalize_request_keys(data)
        return data

    @field_validator("acquisitiondate", mode="before")
    @classmethod
    def parse_acquisitiondate(cls, v: Any) -> Optional[date]:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            return parse_acquisition_date(v)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("cost", mode="before")
    @classmethod
    def parse_cost(cls, v: Any) -> Optional[float]:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            cost = float(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"cost must be a positive number, got: {v!r}") from exc
        # "inf" and "1e400" parse as infinity, which crashed the rounding of the cost fields (an HTTP 500).
        if not math.isfinite(cost):
            raise ValueError(f"cost must be a finite number, got: {v!r}")
        if cost < 0:
            raise ValueError("cost cannot be negative")
        # Far above any real asset in any currency; a band (x1.25 to x8) of anything larger overflows to infinity.
        if cost > 1e18:
            raise ValueError(f"cost is too large, got: {v!r}")
        return cost

    @model_validator(mode="after")
    def validate_check_flags(self) -> AssetAnalysisRequest:
        flags = {name: getattr(self, name) for name in CHECK_FLAGS}
        any_check = any(flags.values())

        if not self.taggingai:
            if any_check:
                raise ValueError("TaggingAI must be true when any check flag is enabled")
            return self

        if not any_check:
            raise ValueError("At least one check flag must be true when TaggingAI is enabled")

        if not _non_empty(self.assetimage) and not _non_empty(self.barcodeimage):
            raise ValueError("At least one of AssetImage or BarcodeImage is required")

        if self.assetnamedescriptionmatch and not _non_empty(self.assetname):
            raise ValueError("AssetName is required when AssetNameDescriptionMatch is true")

        if self.acquisitiondatecostmatch and self.cost is None and self.acquisitiondate is None:
            raise ValueError(
                "AcquisitionCost or AcquisitionDate is required when AcquisitionDateCostMatch is true"
            )

        return self

    def to_validation_plan(self) -> ValidationPlan:
        return ValidationPlan.from_flags({name: getattr(self, name) for name in CHECK_FLAGS})

    def to_request_body(self) -> dict[str, Any]:
        """Flat dict for pipeline (lowercase keys)."""
        data = self.model_dump()
        if self.acquisitiondate:
            data["acquisitiondate"] = self.acquisitiondate
        return data
