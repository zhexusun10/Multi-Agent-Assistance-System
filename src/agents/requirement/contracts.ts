import {z} from 'zod';
import {FIELD_RULES,criterionSchema} from '../../contracts/recommendation-v1.js';
import {locationIntentSchema,PLACE_CATEGORIES} from '../../domain/geo-intake.js';

export const requirementDraftSchema=z.object({
  upsert:z.array(criterionSchema),remove_fields:z.array(z.string()),clarifications:z.array(z.string()).max(3),
  sources:z.array(z.object({field:z.string(),quote:z.string().min(1)}).strict()),supported:z.boolean(),
  location_intents:z.array(locationIntentSchema).max(12).optional(),interpretation_notes:z.array(z.string()).optional(),
  remove_criterion_ids:z.array(z.string()).optional(),
  extraction:z.object({version:z.literal('2.0'),mode:z.literal('model'),attempts:z.number().int().positive(),example_ids:z.array(z.string()),semantic_audit:z.enum(['pending','passed']).optional(),repair_notes:z.array(z.string()).max(2).optional(),coverage:z.array(z.object({quote:z.string(),disposition:z.enum(['extracted','removed','clarification','context'])}).strict())}).strict().optional(),
}).strict();
export type RequirementDraft=z.infer<typeof requirementDraftSchema>;
export const INTAKE_FIELDS=Object.keys(FIELD_RULES).filter(k=>k!=='place_proximity') as [string,...string[]];
export const INTAKE_UNITS=['SGD/month','count','sqft','sqm','year','m','km','min','month'] as const;
const scalar=z.union([z.number().finite().nonnegative(),z.string(),z.boolean()]);
const value=z.union([scalar,z.array(z.number().finite().nonnegative()).min(1).max(2),z.array(z.string()).min(1).max(28)]);
const quote=z.string().min(1).max(4000);
// Every property is required (nullable when inapplicable), for strict tool JSON Schema.
const attribute=z.object({field:z.enum(INTAKE_FIELDS).describe("Ordinary MRT/bus/named-place distances belong in locations. mrt_distance_m and mrt_walking_mins require an explicit request for listing/platform-reported information."),operator:z.enum(['eq','in','gte','lte','between']),value,
  unit:z.enum(INTAKE_UNITS).nullable(),strength:z.enum(['hard','soft']),weight:z.number().positive().finite().nullable(),
  target_name:z.string().min(1).nullable(),transport_mode:z.enum(['transit','walking','driving','unspecified']).nullable(),quote}).strict();
const location=z.object({name:z.string().min(1).max(150).nullable(),categories:z.array(z.enum(PLACE_CATEGORIES)).max(4),
  relation:z.enum(['any_of','all_of']),operator:z.enum(['near','lte','gte','between']),
  value:z.union([z.number().finite().nonnegative(),z.tuple([z.number().finite().nonnegative(),z.number().finite().nonnegative()])]).nullable(),
  unit:z.enum(['m','km']).nullable(),strength:z.enum(['hard','soft']),weight:z.number().positive().finite().nullable(),
  distance_type:z.enum(['straight_line','walking','driving','transit']),explicit_station:z.boolean(),prefer_closer:z.boolean(),quote}).strict();
export const modelIntakeSchema=z.object({
  schema_version:z.literal('2.0'),supported:z.boolean(),upsert:z.array(attribute).max(40),locations:z.array(location).max(12),
  removals:z.array(z.object({field:z.enum(Object.keys(FIELD_RULES) as [string,...string[]]),criterion_id:z.string().min(1).nullable(),quote}).strict()).max(40),
  clarifications:z.array(z.object({quote,question:z.string().min(1).max(500)}).strict()).max(3),
  coverage:z.array(z.object({quote,disposition:z.enum(['extracted','removed','clarification','context'])}).strict()).min(1).max(40),
}).strict();
export const intakeAuditSchema=z.object({valid:z.boolean(),issues:z.array(z.object({quote:z.string(),reason:z.string().min(1)}).strict()).max(8)}).strict();
export type ModelIntake=z.infer<typeof modelIntakeSchema>;
export const INTAKE_ENUMS={
  amenities:['private_bathroom','master_room','common_room','swimming_pool','pet_policy','cooking_allowed','air_conditioning','no_live_in_landlord','balcony','gym','parking','elevator','wifi','washer','refrigerator','security'],
  furnishing:['fully_furnished','partially_furnished','unfurnished'],rental_scope:['whole_unit','room'],
  region_code:['CCR','RCR','OCR'],
  property_type:['Condominium','Apartment','HDB Flat','Walk-up Apartment','Terraced House','Semi-Detached House','Detached House','Bungalow House','Corner Terrace','Shophouse','Good Class Bungalow','Executive Condominium'],
  tenure_category:['freehold','leasehold','999-year leasehold'],
} as const;
