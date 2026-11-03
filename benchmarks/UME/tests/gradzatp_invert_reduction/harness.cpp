// The benchmark, preprocessing and functional API are unchanged pinned snapshots.
#define main ume_stock_main_not_called
#include "gradzatp_invert.cpp"
#undef main
#include "strict_compare.hpp"
#include <cfenv>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <limits>
#include <stdexcept>

#if !defined(FUNC) || !defined(MAA) || !defined(VERIFY) || defined(GEM5) || defined(GEM5_MAGIC) || defined(NDEBUG) || defined(__FAST_MATH__)
#error Require asserted native FUNC/MAA/VERIFY without fast math.
#endif
static_assert(NUM_CORES==4 && TILE_SIZE==16384,"Pinned context and tile size");
static_assert(sizeof(int)==4 && sizeof(float)==4 && sizeof(double)==8 && std::numeric_limits<float>::is_iec559,"Require int32/IEEE float32/binary64");
void require(bool ok,const char* msg){if(!ok)throw std::runtime_error(msg);}
template<class T> std::vector<T> read(const std::string& name){
 std::ifstream in(name,std::ios::binary|std::ios::ate);require(bool(in),"Missing input");auto n=in.tellg();require(n>=0 && n%sizeof(T)==0,"Invalid input extent");
 std::vector<T> a(static_cast<std::size_t>(n)/sizeof(T));in.seekg(0);if(n)in.read(reinterpret_cast<char*>(a.data()),n);require(bool(in),"Input read failed");return a;
}
template<class T> void write(const std::string& name,const std::vector<T>& a){
 std::ofstream out(name,std::ios::binary);if(!a.empty())out.write(reinterpret_cast<const char*>(a.data()),a.size()*sizeof(T));out.close();require(bool(out),"Output write failed");
}
int workers(){int n=0;
#pragma omp parallel
 {
#pragma omp single
 n=omp_get_num_threads();
 }return n;
}
void dump_inputs(const std::string& p){
 write(p+"c_to_p_map.bin",c_to_p_map);write(p+"c_to_z_map.bin",c_to_z_map);write(p+"corner_volume.bin",corner_volume);
 write(p+"csurf.bin",csurf);write(p+"zone_field.bin",zone_field);write(p+"point_type.bin",point_type);write(p+"point_normal.bin",point_normal);
 write(p+"p_to_c_indptr.bin",p_to_c_indptr);write(p+"p_to_c_indices.bin",p_to_c_indices);write(p+"z_to_c_indptr.bin",z_to_c_indptr);write(p+"z_to_c_indices.bin",z_to_c_indices);
}
int main(int argc,char** argv){try{
 require(argc==3,"Usage: harness INPUT_DIR OUTPUT_DIR");require(omp_get_dynamic()==0 && omp_get_max_threads()==1 && workers()==1,"Require one worker");require(std::fegetround()==FE_TONEAREST,"Require nearest rounding");
 std::string in=std::string(argv[1])+"/",out=std::string(argv[2])+"/";
 c_to_p_map=read<int>(in+"c_to_p_map.bin");c_to_z_map=read<int>(in+"c_to_z_map.bin");point_type=read<int>(in+"point_type.bin");
 corner_volume=read<float>(in+"corner_volume.bin");csurf=read<float>(in+"csurf.bin");zone_field=read<float>(in+"zone_field.bin");point_normal=read<float>(in+"point_normal.bin");
 point_volume=read<float>(in+"initial.point_volume.bin");point_gradient=read<float>(in+"initial.point_gradient.bin");point_volume_exp=point_volume;point_gradient_exp=point_gradient;
 const auto np=point_type.size(),nz=zone_field.size(),nc=c_to_p_map.size();
 require(np<=32769 && nc<=65537 && nz<=32769 && c_to_z_map.size()==nc && corner_volume.size()==nc && csurf.size()==nc,"Invalid corner extents");
 require(point_normal.size()==np && point_volume.size()==np && point_gradient.size()==np,"Invalid point extents");
 for(std::size_t c=0;c<nc;c++)require(c_to_p_map[c]>=0 && std::size_t(c_to_p_map[c])<np && c_to_z_map[c]>=0 && std::size_t(c_to_z_map[c])<nz,"Invalid map index");
 init_inverse_map_CSR(int(np),int(nz),int(nc));dump_inputs(out+"before.");
 gradzatp_invert_CSR();dump_inputs(out+"after_baseline.");write(out+"baseline.point_volume.bin",point_volume_exp);write(out+"baseline.point_gradient.bin",point_gradient_exp);
 alloc_MAA();init_MAA();
 tiles0[0]=get_new_tile<int>();tiles1[0]=get_new_tile<int>();tiles2[0]=get_new_tile<int>();tiles3[0]=get_new_tile<int>();tiles4[0]=get_new_tile<int>();tiles5[0]=get_new_tile<int>();tilesi[0]=get_new_tile<int>();
 regs0[0]=get_new_reg<int>();regs1[0]=get_new_reg<int>();regs2[0]=get_new_reg<int>();regs3[0]=get_new_reg<int>();regs4[0]=get_new_reg<int>();last_i_regs[0]=get_new_reg<int>();last_j_regs[0]=get_new_reg<int>();
 gradzatp_invert_MAA_CSR();dump_inputs(out+"after_maa.");write(out+"maa.point_volume.bin",point_volume);write(out+"maa.point_gradient.bin",point_gradient);
 require(workers()==1,"Unexpected worker change");auto v=ume_validation::compare(point_volume_exp,point_volume),g=ume_validation::compare(point_gradient_exp,point_gradient);bool passed=v.passed() && g.passed();
 std::ofstream f(out+"result.json");f<<std::setprecision(17)<<"{\"workers\":1,\"points\":"<<np<<",\"zones\":"<<nz<<",\"corners\":"<<nc<<",\"comparisons\":{\"point_volume\":";
 ume_validation::write_comparison(f,v);f<<",\"point_gradient\":";ume_validation::write_comparison(f,g);f<<"},\"strict_checker_passed\":"<<(passed?"true":"false")<<"}\n";f.close();require(bool(f),"Result write failed");
 std::cout<<(passed?"STRICT_COMPARISON_PASS":"STRICT_COMPARISON_FAIL")<<'\n';return passed?0:1;
 }catch(const std::exception& e){std::cerr<<"HARNESS_ERROR: "<<e.what()<<'\n';return 2;}}
