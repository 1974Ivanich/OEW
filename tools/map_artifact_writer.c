#include "map_artifact_writer.h"
#include <stdio.h>
#include <string.h>
static void put_u16(uint8_t *p,uint16_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);}
static void put_i16(uint8_t *p,int16_t v){put_u16(p,(uint16_t)v);}
static void put_u32(uint8_t *p,uint32_t v){p[0]=(uint8_t)v;p[1]=(uint8_t)(v>>8);p[2]=(uint8_t)(v>>16);p[3]=(uint8_t)(v>>24);}
static bool identity_sane(const OewMapIdentity *i){return i!=NULL&&i->board_revision!=0u&&i->pwm_frequency_hz!=0u&&i->timer_arr!=0u&&i->adc_trigger_id!=0u&&i->adc_clock_hz!=0u&&i->adc_sample_cycles_x2!=0u&&i->adc_config_signature!=0u&&i->current_calibration_signature!=0u;}
static bool provenance_sane(const OewMapProvenance *p){return p!=NULL&&p->characterization_id!=0u&&p->dataset_crc32!=0u&&p->tool_build_id!=0u&&p->qualification_revision!=0u&&p->solver_revision!=0u&&p->certifier_revision!=0u;}
static bool geometry_regions_sane(const OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT])
{
    uint8_t s,w; const uint8_t mode=regions[0][0].reserved;
    if(mode>1u)return false;
    for(s=0u;s<OEW_CURRENT_MAP_SECTOR_COUNT;++s)for(w=0u;w<OEW_CURRENT_MAP_WINDOW_COUNT;++w){
        const OewPwmRegion *r=&regions[s][w];
        if(!r->valid||r->reserved!=mode||r->min_margin_ticks==0u||r->mu_min>r->mu_max||r->mv_min>r->mv_max||r->mw_min>r->mw_max)return false;
        if(mode==1u&&(r->geometry_mod_min_q15<=0||r->geometry_mod_max_q15<=r->geometry_mod_min_q15))return false;
    }
    if(mode==1u)for(s=0u;s<OEW_CURRENT_MAP_SECTOR_COUNT;++s)
        if(regions[s][0].geometry_mod_max_q15>regions[s][1].geometry_mod_min_q15)return false;
    return true;
}

bool MapArtifactWriter_Build(const OewMapIdentity *identity,const OewMapProvenance *provenance,uint8_t startup_sector,uint8_t startup_window,uint16_t startup_hold_cycles,int16_t startup_mu,int16_t startup_mv,int16_t startup_mw,const CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT],const OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT],OewCurrentMap *out)
{
    if(!identity_sane(identity)||!provenance_sane(provenance)||!recon||!regions||!out)return false;
    if(startup_sector>=OEW_CURRENT_MAP_SECTOR_COUNT||startup_window>=OEW_CURRENT_MAP_WINDOW_COUNT||startup_hold_cycles==0u)return false;
    if(!geometry_regions_sane(regions))return false;
    memset(out,0,sizeof(*out)); out->magic=OEW_CURRENT_MAP_MAGIC; out->revision=OEW_CURRENT_MAP_REVISION;
    out->board_revision=identity->board_revision; out->pwm_frequency_hz=identity->pwm_frequency_hz; out->timer_arr=identity->timer_arr; out->adc_trigger_id=identity->adc_trigger_id;
    out->trigger_offset_ticks=identity->trigger_offset_ticks; out->deadtime_ticks=identity->deadtime_ticks; out->adc_clock_hz=identity->adc_clock_hz; out->adc_sample_cycles_x2=identity->adc_sample_cycles_x2;
    out->adc_resolution=identity->adc_resolution; out->adc_config_signature=identity->adc_config_signature; out->current_calibration_signature=identity->current_calibration_signature;
    out->provenance=*provenance; out->startup_sector=startup_sector; out->startup_window=startup_window; out->startup_hold_cycles=startup_hold_cycles; out->startup_mu=startup_mu; out->startup_mv=startup_mv; out->startup_mw=startup_mw;
    memcpy(out->recon,recon,sizeof(out->recon)); memcpy(out->region,regions,sizeof(out->region)); out->crc32=CurrentMap_CalculateCrc32(out); return out->crc32!=0u;
}

size_t MapArtifactWriter_EncodeBinary(const OewCurrentMap *map,uint8_t *dst,size_t capacity)
{
    size_t off=0u; uint8_t sector,window;
#define NEED(n) do{if(capacity-off<(n))return 0u;}while(0)
#define U8(v) do{NEED(1u);dst[off++]=(uint8_t)(v);}while(0)
#define U16(v) do{NEED(2u);put_u16(dst+off,(uint16_t)(v));off+=2u;}while(0)
#define I16(v) do{NEED(2u);put_i16(dst+off,(int16_t)(v));off+=2u;}while(0)
#define U32(v) do{NEED(4u);put_u32(dst+off,(uint32_t)(v));off+=4u;}while(0)
    if(!map||!dst||capacity<OEW_CURRENT_MAP_WIRE_SIZE||map->magic!=OEW_CURRENT_MAP_MAGIC||map->revision!=OEW_CURRENT_MAP_REVISION||map->crc32!=CurrentMap_CalculateCrc32(map))return 0u;
    U32(map->magic);U16(map->revision);U16(map->board_revision);U32(map->pwm_frequency_hz);U32(map->timer_arr);U32(map->adc_trigger_id);U16(map->trigger_offset_ticks);U16(map->deadtime_ticks);U32(map->adc_clock_hz);U16(map->adc_sample_cycles_x2);U8(map->adc_resolution);U32(map->adc_config_signature);U32(map->current_calibration_signature);
    U32(map->provenance.characterization_id);U32(map->provenance.dataset_crc32);U32(map->provenance.tool_build_id);U32(map->provenance.qualification_revision);U32(map->provenance.solver_revision);U32(map->provenance.certifier_revision);
    U8(map->startup_sector);U8(map->startup_window);U16(map->startup_hold_cycles);I16(map->startup_mu);I16(map->startup_mv);I16(map->startup_mw);
    for(sector=0u;sector<OEW_CURRENT_MAP_SECTOR_COUNT;++sector)for(window=0u;window<OEW_CURRENT_MAP_WINDOW_COUNT;++window){const CurrentReconEntry *r=&map->recon[sector][window];U8(r->valid?1u:0u);U8(r->phase_a);U8(r->phase_b);U32((uint32_t)r->m00);U32((uint32_t)r->m01);U32((uint32_t)r->m10);U32((uint32_t)r->m11);}
    for(sector=0u;sector<OEW_CURRENT_MAP_SECTOR_COUNT;++sector)for(window=0u;window<OEW_CURRENT_MAP_WINDOW_COUNT;++window){const OewPwmRegion *r=&map->region[sector][window];I16(r->mu_min);I16(r->mu_max);I16(r->mv_min);I16(r->mv_max);I16(r->mw_min);I16(r->mw_max);U16(r->min_margin_ticks);I16(r->geometry_mod_min_q15);I16(r->geometry_mod_max_q15);U8(r->valid?1u:0u);U8(r->reserved);}
    U32(map->crc32);
#undef U32
#undef I16
#undef U16
#undef U8
#undef NEED
    return off==OEW_CURRENT_MAP_WIRE_SIZE?off:0u;
}

size_t MapArtifactWriter_EncodeAuditJson(const OewCurrentMap *m,char *dst,size_t capacity)
{
    if(!m||!dst||capacity==0u)return 0u;
    int n=snprintf(dst,capacity,"{\n  \"format_version\": %u,\n  \"wire_size\": %u,\n  \"board_revision\": %u,\n  \"pwm_frequency_hz\": %lu,\n  \"timer_arr\": %lu,\n  \"adc_trigger_id\": %lu,\n  \"trigger_offset_ticks\": %u,\n  \"deadtime_ticks\": %u,\n  \"adc_clock_hz\": %lu,\n  \"adc_sample_cycles_x2\": %u,\n  \"adc_resolution\": %u,\n  \"adc_config_signature\": %lu,\n  \"current_calibration_signature\": %lu,\n  \"characterization_id\": %lu,\n  \"dataset_crc32\": %lu,\n  \"tool_build_id\": %lu,\n  \"qualification_revision\": %lu,\n  \"solver_revision\": %lu,\n  \"certifier_revision\": %lu,\n  \"crc32\": %lu\n}\n",(unsigned)OEW_MAP_ARTIFACT_FORMAT_VERSION,(unsigned)OEW_CURRENT_MAP_WIRE_SIZE,(unsigned)m->board_revision,(unsigned long)m->pwm_frequency_hz,(unsigned long)m->timer_arr,(unsigned long)m->adc_trigger_id,(unsigned)m->trigger_offset_ticks,(unsigned)m->deadtime_ticks,(unsigned long)m->adc_clock_hz,(unsigned)m->adc_sample_cycles_x2,(unsigned)m->adc_resolution,(unsigned long)m->adc_config_signature,(unsigned long)m->current_calibration_signature,(unsigned long)m->provenance.characterization_id,(unsigned long)m->provenance.dataset_crc32,(unsigned long)m->provenance.tool_build_id,(unsigned long)m->provenance.qualification_revision,(unsigned long)m->provenance.solver_revision,(unsigned long)m->provenance.certifier_revision,(unsigned long)m->crc32);
    if(n<0||(size_t)n>=capacity)return 0u; return(size_t)n;
}
